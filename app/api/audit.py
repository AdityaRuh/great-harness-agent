"""Audit API — reporting endpoints for pipeline history, decisions, and metrics.

Every screening decision, every checkpoint, every email — documented.
No UPDATE/DELETE on audit_log — append-only for compliance.
"""

import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException

from app.api.pipelines import get_graph, _pipelines
from app.storage import get_pipeline as storage_get_pipeline

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/audit", tags=["Audit"])


@router.get("/pipelines/{pipeline_id}/log")
async def get_audit_log(pipeline_id: str, agent: str | None = None, limit: int = 100):
    """Get the full audit trail for a pipeline.

    Every action by every agent is logged with:
    - agent name
    - action type
    - human-readable explanation (never null)
    - structured data
    - timestamp
    """
    if pipeline_id not in _pipelines:
        _pd = await storage_get_pipeline(pipeline_id)
        if _pd: _pipelines[pipeline_id] = _pd
        else: raise HTTPException(status_code=404, detail="Pipeline not found")

    graph = get_graph()
    config = {"configurable": {"thread_id": pipeline_id}}
    state = await graph.aget_state(config)

    if not state or not state.values:
        raise HTTPException(status_code=404, detail="Pipeline state not found")

    log = state.values.get("audit_log", [])

    # Filter by agent if specified
    if agent:
        log = [entry for entry in log if entry.get("agent") == agent]

    return {
        "pipeline_id": pipeline_id,
        "total_entries": len(log),
        "entries": log[-limit:],
    }


@router.get("/pipelines/{pipeline_id}/report")
async def get_pipeline_report(pipeline_id: str):
    """Generate a comprehensive pipeline report.

    Summarizes: JD generation, screening results, interview outcomes,
    checkpoint decisions, and candidate journey.
    """
    if pipeline_id not in _pipelines:
        _pd = await storage_get_pipeline(pipeline_id)
        if _pd: _pipelines[pipeline_id] = _pd
        else: raise HTTPException(status_code=404, detail="Pipeline not found")

    graph = get_graph()
    config = {"configurable": {"thread_id": pipeline_id}}
    state = await graph.aget_state(config)

    if not state or not state.values:
        raise HTTPException(status_code=404, detail="Pipeline state not found")

    s = state.values
    candidates = s.get("candidates", [])
    ranked = s.get("ranked_candidates", [])
    interviews = s.get("interview_results", [])
    shortlist = s.get("final_shortlist", [])
    audit_log = s.get("audit_log", [])

    # Screening summary
    screened = [c for c in candidates if c.get("screening_result")]
    screening_stats = {
        "total": len(screened),
        "shortlisted": sum(1 for c in screened if "Shortlisted" in (c.get("screening_result", {}).get("verdict", ""))),
        "flagged": sum(1 for c in screened if "Flagged" in (c.get("screening_result", {}).get("verdict", ""))),
        "rejected": sum(1 for c in screened if "Not Shortlisted" in (c.get("screening_result", {}).get("verdict", ""))),
        "avg_score": round(
            sum(c.get("screening_result", {}).get("total_score", 0) for c in screened) / max(len(screened), 1), 1
        ),
        "freshers": sum(1 for c in screened if c.get("candidate_type") == "fresher"),
        "experienced": sum(1 for c in screened if c.get("candidate_type") == "experienced"),
    }

    # Interview summary
    completed_interviews = [i for i in interviews if i.get("status") == "completed"]
    interview_stats = {
        "total_invited": len(interviews),
        "completed": len(completed_interviews),
        "avg_score": round(
            sum(i.get("total_score", 0) for i in completed_interviews) / max(len(completed_interviews), 1), 1
        ),
        "avg_duration_min": round(
            sum(i.get("duration_minutes", 0) or 0 for i in completed_interviews) / max(len(completed_interviews), 1), 1
        ),
    }

    # Checkpoint decisions
    checkpoint_events = [e for e in audit_log if "checkpoint" in e.get("action", "")]

    return {
        "pipeline_id": pipeline_id,
        "role_title": s.get("role_title", ""),
        "status": s.get("status", ""),
        "created": _pipelines.get(pipeline_id, {}).get("created_at"),
        "jd": {
            "drafted": s.get("jd_draft") is not None,
            "approved": s.get("jd_approved"),
            "published_url": s.get("jd_published_url"),
        },
        "screening": screening_stats,
        "ranking": {
            "tier_a": sum(1 for r in ranked if r.get("tier") == "A"),
            "tier_b": sum(1 for r in ranked if r.get("tier") == "B"),
            "total_ranked": len(ranked),
        },
        "interviews": interview_stats,
        "final_shortlist": {
            "count": len(shortlist),
            "top_candidates": [
                {"name": c["name"], "score": c["final_score"], "rank": c["final_rank"]}
                for c in shortlist[:5]
            ],
        },
        "checkpoints": checkpoint_events,
        "audit_entries": len(audit_log),
    }


@router.get("/pipelines/{pipeline_id}/candidate/{candidate_name}/journey")
async def get_candidate_journey(pipeline_id: str, candidate_name: str):
    """Track a single candidate's journey through the pipeline.

    Every touchpoint: parse → screen → rank → invite → interview → verdict.
    """
    if pipeline_id not in _pipelines:
        _pd = await storage_get_pipeline(pipeline_id)
        if _pd: _pipelines[pipeline_id] = _pd
        else: raise HTTPException(status_code=404, detail="Pipeline not found")

    graph = get_graph()
    config = {"configurable": {"thread_id": pipeline_id}}
    state = await graph.aget_state(config)

    if not state or not state.values:
        raise HTTPException(status_code=404, detail="Pipeline state not found")

    s = state.values

    # Find the candidate across all stages
    candidate_data = None
    for c in s.get("candidates", []):
        if c.get("parsed_profile", {}).get("name", "").lower() == candidate_name.lower():
            candidate_data = c
            break

    if not candidate_data:
        raise HTTPException(status_code=404, detail=f"Candidate '{candidate_name}' not found")

    profile = candidate_data.get("parsed_profile", {})
    screening = candidate_data.get("screening_result", {})

    # Find in ranked list
    rank_entry = None
    for r in s.get("ranked_candidates", []):
        if r.get("candidate", {}).get("name", "").lower() == candidate_name.lower():
            rank_entry = r
            break

    # Find interview
    interview_entry = None
    for i in s.get("interview_results", []):
        if i.get("candidate_name", "").lower() == candidate_name.lower():
            interview_entry = i
            break

    # Find in shortlist
    shortlist_entry = None
    for sl in s.get("final_shortlist", []):
        if sl.get("name", "").lower() == candidate_name.lower():
            shortlist_entry = sl
            break

    journey = []

    journey.append({
        "stage": "resume_parsed",
        "data": {
            "name": profile.get("name"),
            "email": profile.get("email"),
            "experience_years": profile.get("total_experience_years"),
            "candidate_type": candidate_data.get("candidate_type"),
            "evidenced_skills": len(profile.get("skills", {}).get("evidenced", [])),
            "extraction_confidence": profile.get("extraction_confidence"),
        },
    })

    if screening:
        journey.append({
            "stage": "screened",
            "data": {
                "procedure": screening.get("procedure"),
                "total_score": screening.get("total_score"),
                "verdict": screening.get("verdict"),
                "escalated": screening.get("escalate_to_human"),
                "red_flags": screening.get("red_flags", []),
                "top_strengths": screening.get("top_strengths", []),
                "key_gaps": screening.get("key_gaps", []),
            },
        })

    if rank_entry:
        journey.append({
            "stage": "ranked",
            "data": {
                "tier": rank_entry.get("tier"),
                "rank": rank_entry.get("final_rank"),
                "composite_score": rank_entry.get("composite_score"),
                "repo_score": rank_entry.get("repo_score"),
            },
        })

    if interview_entry:
        journey.append({
            "stage": "interviewed",
            "data": {
                "status": interview_entry.get("status"),
                "total_score": interview_entry.get("total_score"),
                "questions_answered": interview_entry.get("questions_answered"),
                "duration_minutes": interview_entry.get("duration_minutes"),
                "verdict": interview_entry.get("interview_verdict"),
            },
        })

    if shortlist_entry:
        journey.append({
            "stage": "final_shortlist",
            "data": {
                "final_rank": shortlist_entry.get("final_rank"),
                "final_score": shortlist_entry.get("final_score"),
                "screening_score": shortlist_entry.get("screening_score"),
                "interview_score": shortlist_entry.get("interview_score"),
            },
        })

    return {
        "pipeline_id": pipeline_id,
        "candidate_name": candidate_name,
        "stages_completed": len(journey),
        "journey": journey,
    }


@router.get("/metrics")
async def get_global_metrics():
    """Global metrics across all pipelines."""
    graph = get_graph()

    total_pipelines = len(_pipelines)
    statuses = {}

    for pid in _pipelines:
        config = {"configurable": {"thread_id": pid}}
        try:
            state = await graph.aget_state(config)
            status = state.values.get("status", "unknown") if state and state.values else "unknown"
            statuses[status] = statuses.get(status, 0) + 1
        except Exception:
            statuses["error"] = statuses.get("error", 0) + 1

    return {
        "total_pipelines": total_pipelines,
        "status_breakdown": statuses,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
