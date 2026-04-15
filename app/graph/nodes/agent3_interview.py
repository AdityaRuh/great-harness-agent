"""LangGraph nodes for Agent 3 — AI Avatar Interviewer.

send_invites → generate_questions → run_avatar_interviews →
evaluate_responses → send_notifications → aggregate_shortlist → [CP-3]
"""

import asyncio
import logging

from app.agents.interviewer.invite_sender import send_interview_invites
from app.agents.interviewer.question_generator import generate_interview_questions
from app.agents.interviewer.avatar_session import AvatarInterviewSession
from app.agents.interviewer.notification_sender import send_shortlist_notifications
from app.agents.interviewer.shortlist_aggregator import aggregate_shortlist
from app.graph.state import PipelineState

logger = logging.getLogger(__name__)


def agent3_send_invites(state: PipelineState) -> dict:
    """Send interview invites to ranked candidates."""
    ranked = state.get("ranked_candidates", [])
    if not ranked:
        return {"status": "no_candidates_to_interview",
                "audit_log": [{"agent": "agent_3", "action": "no_candidates",
                    "explanation": "No ranked candidates available for interviews."}]}

    logger.info(f"[Agent 3 / SA-1] Sending invites to {len(ranked)} candidates")

    invited = send_interview_invites(
        ranked_candidates=ranked,
        pipeline_id=state["pipeline_id"],
        role_title=state["role_title"],
    )

    sent_count = sum(1 for i in invited if i.get("email_sent"))

    return {
        "interview_results": invited,  # Store session IDs for tracking
        "status": "invites_sent",
        "audit_log": [{"agent": "agent_3", "action": "invites_sent",
            "explanation": f"Sent {sent_count} interview invites to shortlisted candidates.",
            "data": {"sent": sent_count, "total": len(invited)}}],
    }


def agent3_generate_questions(state: PipelineState) -> dict:
    """Generate tailored questions for each candidate."""
    ranked = state.get("ranked_candidates", [])
    skills_matrix = state.get("skills_matrix", {})
    tech_stack = state.get("tech_stack_profile", {})

    logger.info(f"[Agent 3 / SA-2] Generating questions for {len(ranked)} candidates")

    all_questions = []
    for candidate_entry in ranked:
        profile = candidate_entry.get("candidate", {})
        screening = candidate_entry.get("screening_result", {})
        repo = candidate_entry.get("repo_analysis")

        questions = generate_interview_questions(
            candidate=profile,
            screening_result=screening,
            skills_matrix=skills_matrix,
            tech_stack_profile=tech_stack,
            repo_analysis=repo,
        )

        all_questions.append({
            "candidate_name": profile.get("name", "unknown"),
            "questions": questions,
        })

    return {
        "interview_questions": all_questions,
        "status": "questions_generated",
        "audit_log": [{"agent": "agent_3", "action": "questions_generated",
            "explanation": f"Generated tailored interview questions for {len(all_questions)} candidates. "
                          f"Average {sum(q['questions'].get('total_questions', 0) for q in all_questions) // max(len(all_questions), 1)} questions each.",
            "data": {"candidates": len(all_questions)}}],
    }


def agent3_run_avatar_interviews(state: PipelineState) -> dict:
    """Run avatar interviews via Clawvatar for each candidate.

    Note: In production, interviews run asynchronously — candidates
    click their invite link and interview at their own pace.
    This node tracks completion status.
    """
    questions_map = {q["candidate_name"]: q["questions"] for q in state.get("interview_questions", [])}
    invites = state.get("interview_results", [])

    logger.info(f"[Agent 3 / SA-3] Avatar interviews: {len(invites)} sessions pending")

    # In production, this node would poll for completed sessions.
    # For Phase 3 MVP, we run interviews synchronously.
    results = []
    for invite in invites:
        name = invite.get("candidate_name", "unknown")
        session_id = invite.get("session_id", "")
        questions_data = questions_map.get(name, {})
        questions_list = questions_data.get("questions", [])

        if not questions_list:
            results.append({"candidate_name": name, "session_id": session_id,
                           "status": "no_questions", "total_score": 0})
            continue

        # Create and run avatar session
        session = AvatarInterviewSession(
            session_id=session_id,
            candidate={"name": name, "email": invite.get("email")},
            questions=questions_list,
            role_title=state["role_title"],
        )

        try:
            result = asyncio.get_event_loop().run_until_complete(session.run())
            result["email"] = invite.get("email")
            results.append(result)
        except RuntimeError:
            # Already in async context
            import nest_asyncio
            try:
                nest_asyncio.apply()
                result = asyncio.get_event_loop().run_until_complete(session.run())
                result["email"] = invite.get("email")
                results.append(result)
            except Exception as e:
                logger.warning(f"Avatar interview failed for {name}: {e}")
                results.append({"candidate_name": name, "session_id": session_id,
                               "status": "pending", "total_score": 0,
                               "note": "Interview session created — awaiting candidate completion"})

    completed = sum(1 for r in results if r.get("status") == "completed")

    return {
        "interview_results": results,
        "status": "interviews_in_progress",
        "audit_log": [{"agent": "agent_3", "action": "avatar_interviews",
            "explanation": f"Avatar interview sessions: {completed} completed, "
                          f"{len(results) - completed} pending/failed.",
            "data": {"total": len(results), "completed": completed}}],
    }


def agent3_evaluate_responses(state: PipelineState) -> dict:
    """Aggregate per-candidate interview evaluation."""
    results = state.get("interview_results", [])

    evaluated = []
    for result in results:
        score = result.get("total_score", 0)
        if score >= 50:
            verdict = "shortlisted"
        elif score >= 40:
            verdict = "flagged"
        else:
            verdict = "not_shortlisted"

        evaluated.append({**result, "interview_verdict": verdict})

    shortlisted = sum(1 for e in evaluated if e.get("interview_verdict") == "shortlisted")

    return {
        "interview_results": evaluated,
        "status": "interviews_evaluated",
        "audit_log": [{"agent": "agent_3", "action": "evaluated",
            "explanation": f"Evaluated {len(evaluated)} interviews. {shortlisted} shortlisted.",
            "data": {"total": len(evaluated), "shortlisted": shortlisted}}],
    }


def agent3_send_notifications(state: PipelineState) -> dict:
    """Send shortlist/rejection emails."""
    results = state.get("interview_results", [])

    notif = send_shortlist_notifications(results, state["role_title"])

    return {
        "audit_log": [{"agent": "agent_3", "action": "notifications_sent",
            "explanation": f"Sent notifications: {len(notif['shortlisted'])} shortlisted, "
                          f"{len(notif['rejected'])} rejected.",
            "data": notif}],
    }


def agent3_aggregate_shortlist(state: PipelineState) -> dict:
    """Build final composite shortlist: 0.4 * screening + 0.6 * interview."""
    ranked = state.get("ranked_candidates", [])
    interview_results = state.get("interview_results", [])

    shortlist = aggregate_shortlist(ranked, interview_results)

    return {
        "final_shortlist": shortlist,
        "status": "shortlist_ready",
        "current_checkpoint": "shortlist_confirmation",
        "audit_log": [{"agent": "agent_3", "action": "shortlist_aggregated",
            "explanation": f"Final shortlist: {len(shortlist)} candidates. "
                          f"Top: {shortlist[0]['name']} ({shortlist[0]['final_score']}/100)" if shortlist else "Empty shortlist.",
            "data": {"total": len(shortlist),
                     "top_3": [{"name": s["name"], "score": s["final_score"]} for s in shortlist[:3]]}}],
    }
