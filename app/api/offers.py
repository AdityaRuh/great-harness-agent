"""Agent 4 — Briefing packets, scheduling, and offer/rejection management."""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.integrations.llm import call_llm
from app.integrations.email import send_email
from app.api.interview_eval import _interview_results
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter()

# Store offers
_offers: dict[str, dict] = {}


class OfferDecision(BaseModel):
    session_id: str
    decision: str  # "offer" | "reject"
    salary: str = ""
    start_date: str = ""
    custom_message: str = ""


@router.post("/api/v1/offers/briefing/{session_id}")
async def generate_briefing(session_id: str):
    """Generate a briefing packet for the hiring manager before final interview."""
    result = _interview_results.get(session_id)
    if not result:
        raise HTTPException(status_code=404, detail="Interview result not found")

    evaluation = result.get("evaluation", {})
    transcript = result.get("transcript", [])

    # Build transcript summary
    candidate_answers = [m["text"] for m in transcript if m.get("speaker") in ("You", "Candidate")]
    answers_text = "\n".join(candidate_answers[:10])

    prompt = f"""Generate a concise briefing packet for a hiring manager about this candidate.

Candidate: {result['candidate_name']}
Role: Full Stack Developer at Ruh AI
Interview Score: {result['interview_score']}/100
Verdict: {result['verdict']}
Strengths: {evaluation.get('top_strengths', [])}
Concerns: {evaluation.get('concerns', [])}

Key answers from the interview:
{answers_text[:3000]}

Write a 200-word executive briefing that covers:
1. Candidate summary (2 sentences)
2. Technical assessment (3 sentences)
3. Culture/communication fit (2 sentences)
4. Risk factors (1-2 sentences)
5. Recommendation (hire/pass and why, 2 sentences)

Be direct and honest. This is for the hiring manager's eyes only."""

    briefing = call_llm(prompt)

    logger.info(f"Briefing generated for {result['candidate_name']}")

    # Email briefing to HR
    email_body = f"""
    <div style="font-family:-apple-system,sans-serif;max-width:700px;margin:0 auto;padding:20px">
        <h2 style="color:#1a1a2e">Candidate Briefing: {result['candidate_name']}</h2>
        <div style="background:#f8f9fa;padding:16px;border-radius:8px;margin:16px 0">
            <p><strong>Score:</strong> {result['interview_score']}/100</p>
            <p><strong>Verdict:</strong> {result['verdict']}</p>
        </div>
        <div style="white-space:pre-wrap;line-height:1.6">{briefing}</div>
        <div style="margin-top:24px">
            <a href="{settings.app_url}/dashboard" style="background:#7c6ef0;color:white;padding:10px 24px;border-radius:8px;text-decoration:none;font-weight:600">Review on Dashboard</a>
        </div>
    </div>
    """
    send_email(settings.hr_email, f"Briefing: {result['candidate_name']} — {result['verdict']}", email_body)

    return {
        "session_id": session_id,
        "candidate_name": result["candidate_name"],
        "briefing": briefing,
        "interview_score": result["interview_score"],
        "verdict": result["verdict"],
    }


@router.post("/api/v1/offers/decide")
async def send_offer_or_rejection(data: OfferDecision):
    """Send offer letter or rejection email to candidate."""
    result = _interview_results.get(data.session_id)
    if not result:
        raise HTTPException(status_code=404, detail="Interview result not found")

    candidate_name = result["candidate_name"]
    candidate_email = result.get("candidate_email", "")

    if not candidate_email:
        return {"error": "No candidate email on file"}

    if data.decision == "offer":
        subject = f"Offer Letter — Full Stack Developer at Ruh AI"
        body = f"""
        <div style="font-family:-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:20px">
            <h2 style="color:#1a1a2e">Congratulations, {candidate_name}!</h2>
            <p>We are pleased to offer you the position of <strong>Full Stack Developer</strong> at Ruh AI.</p>
            {f'<p><strong>Compensation:</strong> {data.salary}</p>' if data.salary else ''}
            {f'<p><strong>Start Date:</strong> {data.start_date}</p>' if data.start_date else ''}
            {f'<p>{data.custom_message}</p>' if data.custom_message else ''}
            <p>We were impressed by your technical depth, communication skills, and alignment with our mission of building AI employees that work.</p>
            <p>Please reply to this email to confirm your acceptance or discuss any questions.</p>
            <p>Welcome aboard!<br><strong>Ruh AI Hiring Team</strong></p>
        </div>
        """
    else:
        subject = f"Application Update — Ruh AI"
        body = f"""
        <div style="font-family:-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:20px">
            <h2 style="color:#1a1a2e">Thank You, {candidate_name}</h2>
            <p>Thank you for taking the time to interview for the <strong>Full Stack Developer</strong> position at Ruh AI.</p>
            <p>After careful consideration, we have decided to move forward with other candidates whose experience more closely aligns with our current needs.</p>
            {f'<p>{data.custom_message}</p>' if data.custom_message else ''}
            <p>We encourage you to apply for future openings. Your skills are impressive and we wish you the best in your career.</p>
            <p>Best regards,<br><strong>Ruh AI Hiring Team</strong></p>
        </div>
        """

    email_result = send_email(candidate_email, subject, body)

    _offers[data.session_id] = {
        "candidate_name": candidate_name,
        "decision": data.decision,
        "email_sent": email_result.get("sent", False),
    }

    logger.info(f"{'Offer' if data.decision == 'offer' else 'Rejection'} sent to {candidate_name} ({candidate_email})")

    return {
        "session_id": data.session_id,
        "candidate_name": candidate_name,
        "decision": data.decision,
        "email_sent": email_result.get("sent", False),
    }


@router.get("/api/v1/offers")
async def list_offers():
    return {"total": len(_offers), "offers": list(_offers.values())}
