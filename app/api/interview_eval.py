"""Interview evaluation — receives transcript, scores with Claude, sends notifications."""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.integrations.llm import call_llm_json
from app.integrations.email import send_email
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter()

# Store interview results in memory (keyed by session_id)
_interview_results: dict[str, dict] = {}


class TranscriptSubmission(BaseModel):
    session_id: str
    candidate_name: str
    candidate_email: str = ""
    role_title: str = "Full Stack Developer"
    transcript: list[dict]  # [{"speaker": "AI"|"You", "text": "..."}]


@router.post("/api/v1/interview/evaluate")
async def evaluate_interview(data: TranscriptSubmission):
    """Evaluate a completed interview transcript using Claude."""
    logger.info(f"Evaluating interview for {data.candidate_name} ({len(data.transcript)} messages)")

    # Build transcript text
    transcript_text = ""
    for msg in data.transcript:
        speaker = "Interviewer" if msg["speaker"] == "AI" or msg["speaker"] == "Zara" else "Candidate"
        transcript_text += f"{speaker}: {msg['text']}\n\n"

    if not transcript_text.strip():
        return {"error": "Empty transcript", "score": 0}

    # Evaluate with Claude
    prompt = f"""You are evaluating a technical interview for a {data.role_title} position.

=== CANDIDATE ===
{data.candidate_name}

=== INTERVIEW TRANSCRIPT ===
{transcript_text[:8000]}

=== EVALUATION ===
Score this interview on these dimensions (each 0-100):

1. Technical Knowledge (weight: 0.35) — accuracy, depth, correct use of concepts
2. Problem Solving (weight: 0.25) — approach to challenges, debugging, architecture decisions
3. Communication (weight: 0.20) — clarity, structure, ability to explain technical concepts
4. Culture Fit (weight: 0.10) — enthusiasm, collaboration signals, growth mindset
5. Experience Depth (weight: 0.10) — real examples, ownership, impact

Return JSON:
{{
    "candidate_name": "{data.candidate_name}",
    "technical_knowledge": {{"score": 75, "notes": "..."}},
    "problem_solving": {{"score": 70, "notes": "..."}},
    "communication": {{"score": 80, "notes": "..."}},
    "culture_fit": {{"score": 85, "notes": "..."}},
    "experience_depth": {{"score": 65, "notes": "..."}},
    "total_score": 74,
    "verdict": "Strong Hire | Hire | Lean Hire | Lean No Hire | No Hire",
    "top_strengths": ["strength 1", "strength 2"],
    "concerns": ["concern 1"],
    "recommendation": "One paragraph summary for the hiring manager"
}}

Score honestly. A total_score above 70 = Hire, 55-70 = Lean Hire, below 55 = No Hire.
If the candidate gave very short or empty answers, score accordingly."""

    try:
        evaluation = call_llm_json(prompt)
    except Exception as e:
        logger.error(f"Interview evaluation failed: {e}")
        evaluation = {
            "candidate_name": data.candidate_name,
            "total_score": 0,
            "verdict": "Evaluation Error",
            "recommendation": f"Automated evaluation failed: {e}",
        }

    interview_score = evaluation.get("total_score", 0)
    verdict = evaluation.get("verdict", "Unknown")

    # Store result
    _interview_results[data.session_id] = {
        "candidate_name": data.candidate_name,
        "candidate_email": data.candidate_email,
        "evaluation": evaluation,
        "transcript": data.transcript,
        "interview_score": interview_score,
        "verdict": verdict,
    }

    logger.info(f"Interview evaluated: {data.candidate_name} → {interview_score}/100 → {verdict}")

    # Send result email to candidate
    if data.candidate_email:
        verdict_color = "#4ade80" if interview_score >= 70 else "#fbbf24" if interview_score >= 55 else "#f87171"
        email_body = f"""
        <div style="font-family:-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:20px">
            <h2 style="color:#1a1a2e">Interview Complete — {data.role_title}</h2>
            <p>Hi {data.candidate_name},</p>
            <p>Thank you for completing your AI interview for the <strong>{data.role_title}</strong> position at Ruh AI.</p>
            <p>Your interview has been evaluated. Our hiring team will review the results and get back to you within 2-3 business days.</p>
            <p>Best regards,<br><strong>Ruh AI Hiring Team</strong></p>
        </div>
        """
        send_email(data.candidate_email, f"Interview Complete — {data.role_title}", email_body)

    # Send notification to HR
    hr_body = f"""
    <div style="font-family:-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:20px">
        <h2 style="color:#1a1a2e">Interview Result: {data.candidate_name}</h2>
        <p><strong>Role:</strong> {data.role_title}</p>
        <p><strong>Score:</strong> <span style="color:{verdict_color};font-size:24px;font-weight:bold">{interview_score}/100</span></p>
        <p><strong>Verdict:</strong> {verdict}</p>
        <p><strong>Strengths:</strong> {', '.join(evaluation.get('top_strengths', []))}</p>
        <p><strong>Concerns:</strong> {', '.join(evaluation.get('concerns', []))}</p>
        <p><strong>Recommendation:</strong> {evaluation.get('recommendation', 'N/A')}</p>
        <p><a href="{settings.app_url}/dashboard" style="background:#7c6ef0;color:white;padding:10px 24px;border-radius:8px;text-decoration:none;font-weight:600">Review on Dashboard</a></p>
    </div>
    """
    send_email(settings.hr_email, f"Interview Result: {data.candidate_name} — {verdict}", hr_body)

    return {
        "session_id": data.session_id,
        "candidate_name": data.candidate_name,
        "interview_score": interview_score,
        "verdict": verdict,
        "evaluation": evaluation,
    }


@router.get("/api/v1/interview/results/{session_id}")
async def get_interview_result(session_id: str):
    """Get interview evaluation result."""
    result = _interview_results.get(session_id)
    if not result:
        raise HTTPException(status_code=404, detail="Interview result not found")
    return result


@router.get("/api/v1/interview/results")
async def list_interview_results():
    """List all interview results."""
    return {
        "total": len(_interview_results),
        "results": [
            {
                "session_id": sid,
                "candidate_name": r["candidate_name"],
                "interview_score": r["interview_score"],
                "verdict": r["verdict"],
            }
            for sid, r in _interview_results.items()
        ],
    }
