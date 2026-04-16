"""API routes for pipeline management and checkpoint approval."""

import logging
import uuid
from fastapi import APIRouter, HTTPException
import asyncio
import concurrent.futures

_graph_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
import asyncio
import concurrent.futures
from langgraph.types import Command

from app.schemas.pipeline import PipelineCreate, PipelineResponse, CheckpointApproval
from app.graph.pipeline import build_pipeline

# Will be set after careers module loads
_get_applications = None

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/pipelines", tags=["Pipelines"])

# Build the pipeline graph (singleton)
_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_pipeline()
    return _graph


# In-memory pipeline registry (Phase 1 — replace with DB in Phase 2)
_pipelines: dict[str, dict] = {}


@router.post("", response_model=PipelineResponse)
async def create_pipeline(req: PipelineCreate):
    """Start a new hiring pipeline. Agent 1 begins immediately."""
    pipeline_id = str(uuid.uuid4())
    thread_id = pipeline_id  # LangGraph thread = pipeline

    initial_state = {
        "pipeline_id": pipeline_id,
        "role_title": req.role_title,
        "experience_level": req.experience_level,
        "candidate_type": req.candidate_type,
        "department": req.department,
        "status": "started",
        "current_checkpoint": None,
        "tech_stack_profile": None,
        "skills_matrix": None,
        "jd_draft": None,
        "jd_feedback": None,
        "jd_approved": None,
        "jd_json": None,
        "jd_published_url": None,
        "candidates": [],
        "ranked_candidates": [],
        "ranking_approved": None,
        "interview_questions": [],
        "interview_results": [],
        "final_shortlist": [],
        "shortlist_approved": None,
        "scheduled_interviews": [],
        "verdicts": [],
        "offers_sent": [],
        "rejections_sent": [],
        "audit_log": [],
    }

    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id}}

    logger.info(f"Starting pipeline {pipeline_id}: {req.role_title} ({req.experience_level})")

    # Run the graph in a thread pool so API stays responsive during Claude CLI calls
    result = None
    def _run_graph():
        import asyncio as _aio
        _loop = _aio.new_event_loop()
        _aio.set_event_loop(_loop)
        try:
            results = []
            async def _go():
                async for event in graph.astream(initial_state, config, stream_mode="values"):
                    results.append(event)
            _loop.run_until_complete(_go())
            return results[-1] if results else None
        finally:
            _loop.close()
    result = await asyncio.get_event_loop().run_in_executor(_graph_executor, _run_graph)

    # Store pipeline reference
    _pipelines[pipeline_id] = {
        "id": pipeline_id,
        "thread_id": thread_id,
        "config": {
            "role_title": req.role_title,
            "experience_level": req.experience_level,
            "department": req.department,
            "candidate_type": req.candidate_type,
        },
    }

    return PipelineResponse(
        id=pipeline_id,
        status=result.get("status", "unknown") if result else "started",
        config=_pipelines[pipeline_id]["config"],
        tech_stack_profile=result.get("tech_stack_profile") if result else None,
        skills_matrix=result.get("skills_matrix") if result else None,
        jd_draft=result.get("jd_draft") if result else None,
        jd_published_url=result.get("jd_published_url") if result else None,
        current_checkpoint=result.get("current_checkpoint") if result else None,
        audit_log=result.get("audit_log", []) if result else [],
    )


@router.get("/{pipeline_id}", response_model=PipelineResponse)
async def get_pipeline(pipeline_id: str):
    """Get current state of a pipeline."""
    if pipeline_id not in _pipelines:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    graph = get_graph()
    config = {"configurable": {"thread_id": pipeline_id}}
    state = graph.get_state(config)

    if not state or not state.values:
        raise HTTPException(status_code=404, detail="Pipeline state not found")

    s = state.values
    return PipelineResponse(
        id=pipeline_id,
        status=s.get("status", "unknown"),
        config=_pipelines[pipeline_id]["config"],
        tech_stack_profile=s.get("tech_stack_profile"),
        skills_matrix=s.get("skills_matrix"),
        jd_draft=s.get("jd_draft"),
        jd_published_url=s.get("jd_published_url"),
        current_checkpoint=s.get("current_checkpoint"),
        audit_log=s.get("audit_log", []),
    )


@router.post("/{pipeline_id}/approve")
async def approve_checkpoint(pipeline_id: str, req: CheckpointApproval):
    """Approve or reject a checkpoint. Resumes the pipeline.
    
    For ranking_review: re-runs the ranker on current candidates before advancing,
    so Agent 3 gets the proper ranked list.
    """
    if pipeline_id not in _pipelines:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    graph = get_graph()
    config = {"configurable": {"thread_id": pipeline_id}}

    state = graph.get_state(config)
    if not state or not state.next:
        raise HTTPException(status_code=400, detail="Pipeline is not waiting at a checkpoint")

    logger.info(f"Resolving checkpoint '{req.checkpoint}' for pipeline {pipeline_id}: {req.decision}")

    # Before advancing past ranking_review: re-run ranker + filter by HR decisions
    if state.values.get("current_checkpoint") == "ranking_review":
        candidates = state.values.get("candidates", [])
        if candidates:
            from app.agents.screener.ranker import rank_candidates
            from app.api.candidates import _hr_decisions

            decisions = _hr_decisions.get(pipeline_id, {})

            # Only skip HR-rejected candidates. Everyone else advances:
            # Shortlisted, Flagged, Approved, No action → all get interview invite
            # Rejected by HR → skip (rejection email already sent)
            approved = []
            for cand in candidates:
                cd = cand.get("candidate", cand.get("parsed_profile", cand))
                email = cd.get("email", "")
                name = cd.get("name", "")
                hr = decisions.get(email) or decisions.get(name) or {}

                if hr.get("decision") == "reject":
                    logger.info(f"Skip rejected: {name}")
                    continue
                approved.append(cand)

            ranked = rank_candidates(approved, state.values.get("tech_stack_profile", {}))
            try:
                async for _ in graph.astream(
                    Command(update={"ranked_candidates": ranked}),
                    config, stream_mode="values",
                ):
                    pass
                logger.info(f"Advancing {len(approved)}/{len(candidates)} candidates (rest rejected/held/unreviewed)")
            except Exception as e:
                logger.warning(f"Re-ranking update failed: {e}")

    # Resume the graph with HR's decision
    resume_value = {
        "decision": req.decision,
        "feedback": req.feedback,
        "verdicts": req.verdicts,
    }

    result = None
    def _run_approve():
        import asyncio as _aio
        _loop = _aio.new_event_loop()
        _aio.set_event_loop(_loop)
        try:
            results = []
            async def _go():
                async for event in graph.astream(
                    Command(resume=resume_value),
                    config,
                    stream_mode="values",
                ):
                    results.append(event)
            _loop.run_until_complete(_go())
            return results[-1] if results else None
        finally:
            _loop.close()
    result = await asyncio.get_event_loop().run_in_executor(_graph_executor, _run_approve)

    return {
        "pipeline_id": pipeline_id,
        "checkpoint": req.checkpoint,
        "decision": req.decision,
        "new_status": result.get("status") if result else "unknown",
        "next_checkpoint": result.get("current_checkpoint") if result else None,
    }


@router.get("")
async def list_pipelines():
    """List all pipelines."""
    items = []
    graph = get_graph()
    for pid, pdata in _pipelines.items():
        config = {"configurable": {"thread_id": pid}}
        try:
            state = graph.get_state(config)
            status = state.values.get("status", "unknown") if state and state.values else "unknown"
        except Exception:
            status = "unknown"
        items.append({
            "id": pid,
            "role_title": pdata["config"]["role_title"],
            "status": status,
        })
    return items
