"""API routes for pipeline management and checkpoint approval."""

import logging
from datetime import datetime, timezone
import uuid
from fastapi import APIRouter, HTTPException
from langgraph.types import Command

from app.schemas.pipeline import PipelineCreate, PipelineResponse, CheckpointApproval
from app.graph.pipeline import build_pipeline
from app.storage import (
    _mem_pipelines as _pipelines,
    _mem_running_pipelines as _running_pipelines,
    save_pipeline as storage_save_pipeline,
    update_pipeline_cache as storage_update_cache,
)

# Will be set after careers module loads
_get_applications = None

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/pipelines", tags=["Pipelines"])

# Build the pipeline graph (singleton)
_graph = None


def get_graph(checkpointer=None):
    global _graph
    if _graph is None:
        _graph = build_pipeline(checkpointer=checkpointer)
    return _graph


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

    # Get graph with async checkpointer from app state if available
    from starlette.requests import Request as _Req
    _cp = None
    try:
        from app.main import app as _app
        _cp = getattr(_app.state, "checkpointer", None)
    except Exception:
        pass
    graph = get_graph(checkpointer=_cp)
    config = {"configurable": {"thread_id": thread_id}}

    # Store pipeline reference BEFORE graph runs (so Worker 2 can find it)
    pipeline_config = {
        "role_title": req.role_title,
        "experience_level": req.experience_level,
        "department": req.department,
        "candidate_type": req.candidate_type,
    }
    _pipelines[pipeline_id] = {
        "id": pipeline_id,
        "thread_id": thread_id,
        "config": pipeline_config,
        "last_status": "starting",
        "last_state_cache": {},
        "created_at": "",
    }
    try:
        await storage_save_pipeline(pipeline_id, pipeline_config, "starting")
    except Exception as e:
        logger.warning(f"Early DB save failed: {e}")

    logger.info(f"Starting pipeline {pipeline_id}: {req.role_title} ({req.experience_level})")

    # Run the pipeline graph
    _running_pipelines.add(pipeline_id)
    result = None
    try:
        async for event in graph.astream(initial_state, config, stream_mode="values"):
            result = event
    finally:
        _running_pipelines.discard(pipeline_id)

    # Update pipeline with final state
    _pipelines[pipeline_id]["last_status"] = result.get("status", "unknown") if result else "unknown"
    try:
        await storage_save_pipeline(pipeline_id, pipeline_config, result.get("status", "unknown") if result else "unknown")
    except Exception:
        pass

    return PipelineResponse(
        id=pipeline_id,
        status=result.get("status", "unknown") if result else "started",
        config=pipeline_config,
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
        from app.storage import get_pipeline as storage_get
        pdata_db = await storage_get(pipeline_id)
        if pdata_db:
            _pipelines[pipeline_id] = pdata_db
        else:
            raise HTTPException(status_code=404, detail="Pipeline not found")

    pdata = _pipelines[pipeline_id]

    # If graph is actively running, return cached state to avoid blocking
    if pipeline_id in _running_pipelines:
        cached = pdata.get("last_state_cache", {})
        return PipelineResponse(
            id=pipeline_id,
            status=cached.get("status", pdata.get("last_status", "processing")),
            config=pdata["config"],
            tech_stack_profile=cached.get("tech_stack_profile"),
            skills_matrix=cached.get("skills_matrix"),
            jd_draft=cached.get("jd_draft"),
            jd_published_url=cached.get("jd_published_url"),
            current_checkpoint=cached.get("current_checkpoint"),
            audit_log=cached.get("audit_log", []),
        )

    graph = get_graph()
    config = {"configurable": {"thread_id": pipeline_id}}
    state = graph.get_state(config)

    if not state or not state.values:
        raise HTTPException(status_code=404, detail="Pipeline state not found")

    s = state.values

    # Cache state for when graph is running
    cache = {k: s.get(k) for k in ["status","current_checkpoint","tech_stack_profile","skills_matrix","jd_draft","jd_published_url","audit_log","candidates","ranked_candidates"]}
    pdata["last_state_cache"] = cache
    pdata["last_status"] = s.get("status", "unknown")
    try:
        await storage_update_cache(pipeline_id, s.get("status", "unknown"), cache)
    except Exception:
        pass

    return PipelineResponse(
        id=pipeline_id,
        status=s.get("status", "unknown"),
        config=pdata["config"],
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
        from app.storage import get_pipeline as storage_get3
        pdata_db = await storage_get3(pipeline_id)
        if pdata_db:
            _pipelines[pipeline_id] = pdata_db
        else:
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

    _running_pipelines.add(pipeline_id)
    result = None
    try:
        async for event in graph.astream(
            Command(resume=resume_value),
            config,
            stream_mode="values",
        ):
            result = event
    finally:
        _running_pipelines.discard(pipeline_id)

    # Cache final state after graph completes
    if result and pipeline_id in _pipelines:
        _pipelines[pipeline_id]["last_status"] = result.get("status", "unknown")
        _pipelines[pipeline_id]["last_state_cache"] = {k: result.get(k) for k in ["status","current_checkpoint","tech_stack_profile","skills_matrix","jd_draft","jd_published_url","audit_log","candidates","ranked_candidates"]}

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
    # Sync pipelines from DB if this worker is missing some
    try:
        from app.storage import list_all_pipelines, _mem_pipelines
        from app.db import list_pipelines_db, _async_engine
        if _async_engine:
            db_pipelines = await list_pipelines_db()
            for dp in db_pipelines:
                if dp["id"] not in _mem_pipelines:
                    _mem_pipelines[dp["id"]] = dp
    except Exception:
        pass

    items = []
    graph = get_graph()
    for pid, pdata in _pipelines.items():
        if pid in _running_pipelines:
            # Don't call graph.get_state while graph is running — use cached status
            items.append({
                "id": pid,
                "role_title": pdata["config"]["role_title"],
                "status": pdata.get("last_status", "processing"),
            })
            continue
        config = {"configurable": {"thread_id": pid}}
        try:
            state = graph.get_state(config)
            status = state.values.get("status", "unknown") if state and state.values else "unknown"
            pdata["last_status"] = status  # cache it
        except Exception:
            status = pdata.get("last_status", "unknown")
        items.append({
            "id": pid,
            "role_title": pdata["config"]["role_title"],
            "status": status,
        })
    return items
