"""Checkpoint nodes — human-in-the-loop gates using LangGraph interrupt."""

import logging
from langgraph.types import interrupt
from app.graph.state import PipelineState

logger = logging.getLogger(__name__)


def checkpoint_jd_approval(state: PipelineState) -> dict:
    """Pauses pipeline. HR approves or requests changes to JD."""
    logger.info("[Checkpoint 1] JD approval — waiting for HR")

    decision = interrupt(value={
        "checkpoint": "jd_approval",
        "pipeline_id": state["pipeline_id"],
        "role_title": state["role_title"],
        "jd_preview": state["jd_draft"][:2000] if state.get("jd_draft") else "",
        "message": "Please review the generated JD. Approve or provide feedback.",
    })

    approved = decision.get("decision") == "approve"
    feedback = decision.get("feedback")

    return {
        "jd_approved": approved,
        "jd_feedback": feedback if not approved else None,
        "status": "jd_approved" if approved else "jd_revision_needed",
        "current_checkpoint": None,
        "audit_log": [{
            "agent": "orchestrator",
            "action": "checkpoint_jd_resolved",
            "explanation": f"HR {'approved' if approved else 'requested changes to'} the JD."
                          + (f" Feedback: {feedback}" if feedback else ""),
            "data": {"approved": approved, "feedback": feedback},
        }],
    }


def checkpoint_ranking_review(state: PipelineState) -> dict:
    """Pauses pipeline. HR reviews ranked candidate list."""
    logger.info("[Checkpoint 2] Ranking review — waiting for HR")

    decision = interrupt(value={
        "checkpoint": "ranking_review",
        "pipeline_id": state["pipeline_id"],
        "total_candidates": len(state.get("ranked_candidates", [])),
        "message": "Review the ranked candidate list. Approve to proceed to interviews.",
    })

    return {
        "ranking_approved": decision.get("decision") == "approve",
        "status": "ranking_approved",
        "current_checkpoint": None,
        "audit_log": [{
            "agent": "orchestrator",
            "action": "checkpoint_ranking_resolved",
            "explanation": f"HR {'approved' if decision.get('decision') == 'approve' else 'rejected'} rankings.",
            "data": decision,
        }],
    }


def checkpoint_shortlist(state: PipelineState) -> dict:
    """Pauses pipeline. HR confirms final shortlist after interviews."""
    logger.info("[Checkpoint 3] Shortlist confirmation — waiting for HR")

    decision = interrupt(value={
        "checkpoint": "shortlist_confirmation",
        "pipeline_id": state["pipeline_id"],
        "shortlisted_count": len(state.get("final_shortlist", [])),
        "message": "Review the interview results. Approve shortlist for final interviews.",
    })

    return {
        "shortlist_approved": decision.get("decision") == "approve",
        "status": "shortlist_approved",
        "current_checkpoint": None,
        "audit_log": [{
            "agent": "orchestrator",
            "action": "checkpoint_shortlist_resolved",
            "explanation": f"HR confirmed shortlist.",
            "data": decision,
        }],
    }


def checkpoint_verdict(state: PipelineState) -> dict:
    """Pauses pipeline. Interviewer submits final verdict."""
    logger.info("[Checkpoint 4] Final verdict — waiting for interviewer")

    decision = interrupt(value={
        "checkpoint": "final_verdict",
        "pipeline_id": state["pipeline_id"],
        "message": "Submit your interview verdict for each candidate.",
    })

    return {
        "verdicts": decision.get("verdicts", []),
        "status": "verdicts_received",
        "current_checkpoint": None,
        "audit_log": [{
            "agent": "orchestrator",
            "action": "checkpoint_verdict_resolved",
            "explanation": f"Interviewer submitted {len(decision.get('verdicts', []))} verdicts.",
            "data": decision,
        }],
    }
