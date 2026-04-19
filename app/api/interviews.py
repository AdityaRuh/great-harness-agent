"""API routes for interview session management."""

import logging
from fastapi import APIRouter, HTTPException

from app.api.pipelines import get_graph, _pipelines

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/pipelines", tags=["Interviews"])


@router.get("/{pipeline_id}/interviews")
async def list_interviews(pipeline_id: str):
    """List all interview sessions for a pipeline."""
    if pipeline_id not in _pipelines:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    graph = get_graph()
    config = {"configurable": {"thread_id": pipeline_id}}
    state = await graph.aget_state(config)

    if not state or not state.values:
        raise HTTPException(status_code=404, detail="Pipeline state not found")

    results = state.values.get("interview_results", [])
    questions = state.values.get("interview_questions", [])

    return {
        "pipeline_id": pipeline_id,
        "total_sessions": len(results),
        "completed": sum(1 for r in results if r.get("status") == "completed"),
        "pending": sum(1 for r in results if r.get("status") == "pending"),
        "questions_generated_for": len(questions),
        "sessions": [
            {
                "session_id": r.get("session_id"),
                "candidate_name": r.get("candidate_name"),
                "status": r.get("status"),
                "total_score": r.get("total_score"),
                "questions_asked": r.get("questions_asked"),
                "questions_answered": r.get("questions_answered"),
                "duration_minutes": r.get("duration_minutes"),
                "interview_verdict": r.get("interview_verdict"),
            }
            for r in results
        ],
    }


@router.get("/{pipeline_id}/interviews/{session_id}")
async def get_interview_detail(pipeline_id: str, session_id: str):
    """Get detailed interview session including transcript and scores."""
    if pipeline_id not in _pipelines:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    graph = get_graph()
    config = {"configurable": {"thread_id": pipeline_id}}
    state = await graph.aget_state(config)

    if not state or not state.values:
        raise HTTPException(status_code=404, detail="Pipeline state not found")

    results = state.values.get("interview_results", [])
    session = next((r for r in results if r.get("session_id") == session_id), None)

    if not session:
        raise HTTPException(status_code=404, detail="Interview session not found")

    return session


@router.get("/{pipeline_id}/shortlist")
async def get_shortlist(pipeline_id: str):
    """Get final shortlist (screening + interview composite scores)."""
    if pipeline_id not in _pipelines:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    graph = get_graph()
    config = {"configurable": {"thread_id": pipeline_id}}
    state = await graph.aget_state(config)

    if not state or not state.values:
        raise HTTPException(status_code=404, detail="Pipeline state not found")

    shortlist = state.values.get("final_shortlist", [])
    if not shortlist:
        raise HTTPException(status_code=400, detail="Shortlist not yet available")

    return {
        "pipeline_id": pipeline_id,
        "total": len(shortlist),
        "formula": "final_score = 0.4 * screening + 0.6 * interview",
        "shortlist": shortlist,
    }
