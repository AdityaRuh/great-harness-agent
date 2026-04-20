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
        # Try to get async checkpointer from app state
        if checkpointer is None:
            try:
                from app.main import app as _app
                checkpointer = getattr(_app.state, "checkpointer", None)
            except Exception:
                pass
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
            "interview_rounds": req.interview_rounds,
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

    # Store pipeline reference BEFORE graph runs (so Worker 2 can find it)
    pipeline_config = {
        "role_title": req.role_title,
        "experience_level": req.experience_level,
        "department": req.department,
        "candidate_type": req.candidate_type,
            "interview_rounds": req.interview_rounds,
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


@router.delete("/{pipeline_id}")
async def delete_pipeline(pipeline_id: str):
    """Delete a pipeline and all associated data."""
    # Remove from memory
    _pipelines.pop(pipeline_id, None)
    _running_pipelines.discard(pipeline_id)

    # Remove from DB
    try:
        from app.db import delete_pipeline_db
        await delete_pipeline_db(pipeline_id)
    except Exception as e:
        logger.warning(f"DB delete failed: {e}")

    # Remove associated interview data
    try:
        from app.storage import delete_pipeline_data
        await delete_pipeline_data(pipeline_id)
    except Exception:
        pass

    logger.info(f"Pipeline {pipeline_id} deleted")
    return {"status": "deleted", "id": pipeline_id}


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

    graph = get_graph()
    config = {"configurable": {"thread_id": pipeline_id}}
    state = await graph.aget_state(config)

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

    state = await graph.aget_state(config)
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

    # Auto-approve shortlist if this was a shortlist checkpoint
    try:
        from app.api.interview_eval import _interview_shortlist_approved
        _interview_shortlist_approved.add(pipeline_id)
    except Exception:
        pass

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


@router.get("/stats")
async def pipeline_stats():
    """Aggregate stats for the dashboard overview."""
    total_pipelines = len(_pipelines)
    total_candidates = 0
    total_interviews = 0
    total_offers = 0
    
    graph = get_graph()
    for pid, pdata in _pipelines.items():
        try:
            config = {"configurable": {"thread_id": pid}}
            state = await graph.aget_state(config)
            if state and state.values:
                s = state.values
                total_candidates += len(s.get("candidates", []))
                total_interviews += len(s.get("scheduled_interviews", []))
                total_offers += len(s.get("offers_sent", []))
        except Exception:
            pass
    
    # Also count from interview results
    try:
        from app.storage import _mem_interview_results, _mem_scheduled
        total_interviews = max(total_interviews, len(_mem_interview_results))
        total_offers = max(total_offers, len(_mem_scheduled))
    except Exception:
        pass
    
    return {
        "pipelines": total_pipelines,
        "candidates": total_candidates,
        "interviews": total_interviews,
        "offers": total_offers,
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
    for pid, pdata in _pipelines.items():
        # Use cached status for fast list response — detail view has fresh state
        items.append({
            "id": pid,
            "role_title": pdata.get("config", {}).get("role_title", "Untitled"),
            "status": pdata.get("last_status", "unknown"),
            "config": pdata.get("config", {}),
            "created_at": pdata.get("created_at", ""),
        })
    return items
