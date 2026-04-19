"""API routes for candidate management — resume upload."""

import logging
from typing import List
from fastapi import APIRouter, HTTPException, UploadFile, File

from app.api.pipelines import get_graph, _pipelines
from app.storage import get_pipeline as storage_get_pipeline

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/pipelines", tags=["Candidates"])


@router.post("/{pipeline_id}/upload")
async def upload_resumes(pipeline_id: str, files: List[UploadFile] = File(...)):
    """Upload one or more resume files (PDF/DOCX) to a pipeline.

    The pipeline must exist and have a published JD (Agent 1 complete).
    Uploaded resumes are stored in the pipeline state and processed
    when the graph advances through Agent 2 nodes.
    """
    if pipeline_id not in _pipelines:
        _pd = await storage_get_pipeline(pipeline_id)
        if _pd: _pipelines[pipeline_id] = _pd
        else: raise HTTPException(status_code=404, detail="Pipeline not found")

    graph = get_graph()
    config = {"configurable": {"thread_id": pipeline_id}}

    # Get current state
    state = graph.get_state(config)
    if not state or not state.values:
        raise HTTPException(status_code=400, detail="Pipeline state not found")

    current_status = state.values.get("status", "")
    if current_status not in ("jd_published", "screening_no_candidates", "ranking_complete"):
        logger.info(f"Upload at status '{current_status}' — proceeding anyway")

    # Process uploaded files
    candidates = state.values.get("candidates", [])
    uploaded = []

    for f in files:
        if not f.filename:
            continue

        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
        if ext not in ("pdf", "docx", "doc"):
            logger.warning(f"Skipping unsupported file: {f.filename}")
            continue

        content = await f.read()
        candidate_entry = {
            "filename": f.filename,
            "file_bytes": content,
            "file_size": len(content),
            "parsed_profile": None,
            "screening_result": None,
            "candidate_type": None,
        }
        candidates.append(candidate_entry)
        uploaded.append({"filename": f.filename, "size": len(content)})
        logger.info(f"Resume uploaded: {f.filename} ({len(content)} bytes)")

    if not uploaded:
        raise HTTPException(status_code=400, detail="No valid resume files uploaded (PDF/DOCX only)")

    # Update pipeline state with the new candidates
    # We need to update the state and then trigger Agent 2 processing
    from langgraph.types import Command

    # Update candidates in state and trigger Agent 2
    update = {"candidates": candidates}

    result = None
    async for event in graph.astream(
        Command(update=update),
        config,
        stream_mode="values",
    ):
        result = event

    return {
        "pipeline_id": pipeline_id,
        "uploaded": len(uploaded),
        "total_candidates": len(candidates),
        "files": uploaded,
        "status": result.get("status") if result else "resumes_uploaded",
        "message": f"{len(uploaded)} resumes uploaded. Agent 2 screening will begin.",
    }


@router.get("/{pipeline_id}/candidates")
async def list_candidates(pipeline_id: str):
    """List all candidates in a pipeline with their screening status."""
    if pipeline_id not in _pipelines:
        _pd = await storage_get_pipeline(pipeline_id)
        if _pd: _pipelines[pipeline_id] = _pd
        else: raise HTTPException(status_code=404, detail="Pipeline not found")

    graph = get_graph()
    config = {"configurable": {"thread_id": pipeline_id}}
    state = graph.get_state(config)

    if not state or not state.values:
        raise HTTPException(status_code=404, detail="Pipeline state not found")

    candidates = state.values.get("candidates", [])
    ranked = state.values.get("ranked_candidates", [])

    result = []
    for c in candidates:
        profile = c.get("candidate", c.get("parsed_profile", {}))
        app_data = c.get("application_data", {})
        screening = c.get("screening_result", {})
        result.append({
            "name": profile.get("name") or app_data.get("name", "unknown"),
            "email": profile.get("email") or app_data.get("email"),
            "candidate_type": c.get("candidate_type"),
            "screening_score": screening.get("total_score"),
            "screening_verdict": screening.get("verdict"),
            "escalated": screening.get("escalate_to_human", False),
            "red_flags": screening.get("red_flags", []),
            "decision_note": screening.get("decision_note"),
        })

    return {
        "pipeline_id": pipeline_id,
        "total": len(result),
        "candidates": result,
        "ranking_available": len(ranked) > 0,
    }


@router.get("/{pipeline_id}/rankings")
async def get_rankings(pipeline_id: str):
    """Get the ranked candidate list (after Agent 2 completes)."""
    if pipeline_id not in _pipelines:
        _pd = await storage_get_pipeline(pipeline_id)
        if _pd: _pipelines[pipeline_id] = _pd
        else: raise HTTPException(status_code=404, detail="Pipeline not found")

    graph = get_graph()
    config = {"configurable": {"thread_id": pipeline_id}}
    state = graph.get_state(config)

    if not state or not state.values:
        raise HTTPException(status_code=404, detail="Pipeline state not found")

    ranked = state.values.get("ranked_candidates", [])
    if not ranked:
        raise HTTPException(status_code=400, detail="Rankings not yet available. Upload resumes first.")

    return {
        "pipeline_id": pipeline_id,
        "total_ranked": len(ranked),
        "tier_a_count": sum(1 for r in ranked if r.get("tier") == "A"),
        "tier_b_count": sum(1 for r in ranked if r.get("tier") == "B"),
        "rankings": [
            {
                "rank": r.get("final_rank"),
                "name": r.get("candidate", {}).get("name", "unknown"),
                "tier": r.get("tier"),
                "composite_score": r.get("composite_score"),
                "screening_score": r.get("screening_result", {}).get("total_score"),
                "repo_score": r.get("repo_score"),
                "verdict": r.get("screening_result", {}).get("verdict"),
                "escalated": r.get("screening_result", {}).get("escalate_to_human", False),
            }
            for r in ranked
        ],
    }


# Store HR decisions per pipeline
_hr_decisions: dict[str, dict[str, dict]] = {}  # {pipeline_id: {candidate_email: {decision, note}}}


@router.post("/{pipeline_id}/candidates/review")
async def review_candidate(pipeline_id: str, body: dict):
    """HR reviews a candidate — approve, reject, or hold."""
    email = body.get("email", "")
    name = body.get("name", "")
    decision = body.get("decision", "")  # approve | reject | hold
    note = body.get("note", "")

    if not decision:
        raise HTTPException(status_code=400, detail="Decision required")

    _hr_decisions.setdefault(pipeline_id, {})[email or name] = {
        "name": name,
        "email": email,
        "decision": decision,
        "note": note,
    }

    logger.info(f"HR decision for {name}: {decision}" + (f" — {note}" if note else ""))

    # If rejected, send rejection email immediately
    if decision == "reject" and email:
        from app.integrations.email import send_email
        send_email(
            to=email,
            subject="Application Update — Ruh AI",
            body_html=f"""
            <div style="font-family:-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:20px">
                <h2 style="color:#1a1a2e">Thank You, {name}</h2>
                <p>Thank you for your interest in the position at Ruh AI.</p>
                <p>After careful review, we've decided to move forward with other candidates whose experience more closely aligns with our current needs.</p>
                <p>We encourage you to apply for future openings.</p>
                <p>Best regards,<br><strong>Ruh AI Hiring Team</strong></p>
            </div>""",
        )
        logger.info(f"Rejection email sent to {name} ({email})")

    return {"name": name, "decision": decision, "note": note}


@router.get("/{pipeline_id}/candidates/decisions")
async def get_decisions(pipeline_id: str):
    """Get all HR decisions for a pipeline."""
    return _hr_decisions.get(pipeline_id, {})
