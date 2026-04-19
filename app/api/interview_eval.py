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

# Store generated questions per session
_interview_questions: dict[str, list] = {}
_interview_hr_decisions: dict[str, dict] = {}  # HR qualify/reject decisions
_interview_shortlist_approved: set = set()  # pipeline_ids where HR approved shortlist
_interview_question_meta: dict[str, dict] = {}  # {session_id: {name, email}}


class TranscriptSubmission(BaseModel):
    session_id: str
    candidate_name: str
    candidate_email: str = ""
    role_title: str = "Full Stack Developer"
    transcript: list[dict]  # [{"speaker": "AI"|"You", "text": "..."}]




@router.get("/api/v1/interview/questions/{session_id}")
async def get_interview_questions(session_id: str):
    """Get generated questions for an interview session."""
    questions = _interview_questions.get(session_id, [])
    if not questions:
        try:
            from app.storage import get_interview_questions as storage_get_q
            questions, meta_db = await storage_get_q(session_id)
            if questions:
                _interview_questions[session_id] = questions
                if meta_db:
                    _interview_question_meta[session_id] = meta_db
        except Exception:
            pass
    # Also return candidate info if available
    meta = _interview_question_meta.get(session_id, {})
    return {"session_id": session_id, "questions": questions, "total": len(questions),
            "candidate_name": meta.get("name", ""), "candidate_email": meta.get("email", ""),
            "screening_score": meta.get("screening_score", 0)}



@router.get("/api/v1/interview/pending")
async def get_pending_interviews():
    """Get interviews that are pending (invite sent but not completed)."""
    pending = []
    # Also check DB for pending interviews
    if not _interview_question_meta:
        try:
            from app.storage import list_pending_interviews as storage_pending
            db_pending = await storage_pending()
            return {"pending": db_pending, "total": len(db_pending)}
        except Exception:
            pass
    for sid, meta in _interview_question_meta.items():
        if sid not in _interview_results:
            pending.append({
                "session_id": sid,
                "candidate_name": meta.get("name", "Unknown"),
                "candidate_email": meta.get("email", ""),
                "screening_score": meta.get("screening_score", 0),
                "status": "invite_sent",
                "questions_count": len(_interview_questions.get(sid, [])),
            })
    return {"pending": pending, "total": len(pending)}

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

    # Calculate composite: 80% screening + 20% interview
    screening_score = 0
    # First try session metadata (set when invite was sent)
    meta = _interview_question_meta.get(data.session_id, {})
    # Multi-worker: fetch from DB if not in local memory
    if not meta:
        try:
            from app.storage import get_interview_questions as _get_q
            _, meta = await _get_q(data.session_id)
            if meta:
                _interview_question_meta[data.session_id] = meta
        except Exception:
            pass
    # Use meta name if POST body has generic name
    if meta.get("name") and (not data.candidate_name or data.candidate_name in ("Candidate", "Unknown", "")):
        data.candidate_name = meta["name"]
    if meta.get("email") and not data.candidate_email:
        data.candidate_email = meta["email"]
    if meta.get("screening_score"):
        screening_score = meta["screening_score"]
    else:
        # Fallback: search applications
        try:
            from app.api.careers import _applications
            for pid, apps in _applications.items():
                for app in apps:
                    app_name = app.get("name", "")
                    app_email = app.get("email", "")
                    cand = app.get("candidate", {})
                    if (app_name == data.candidate_name or app_email == data.candidate_email
                        or cand.get("name") == data.candidate_name or cand.get("email") == data.candidate_email):
                        sr = app.get("screening_result", {})
                        screening_score = sr.get("total_score", 0)
                        break
        except Exception:
            pass

    composite_score = round(0.8 * screening_score + 0.2 * interview_score)
    shortlisted = composite_score >= 50
    shortlist_verdict = "Shortlisted for Final Round" if shortlisted else "Not Shortlisted"

    evaluation["composite_score"] = composite_score
    evaluation["screening_score"] = screening_score
    evaluation["interview_score"] = interview_score
    evaluation["shortlisted"] = shortlisted
    evaluation["shortlist_verdict"] = shortlist_verdict
    evaluation["composite_formula"] = "80% screening + 20% interview"

    _interview_results[data.session_id]["composite_score"] = composite_score
    _interview_results[data.session_id]["screening_score"] = screening_score
    _interview_results[data.session_id]["shortlisted"] = shortlisted
    _interview_results[data.session_id]["shortlist_verdict"] = shortlist_verdict

    # Persist to DB for multi-worker
    try:
        from app.storage import save_interview_result as _save_ir
        await _save_ir(data.session_id, _interview_results[data.session_id])
    except Exception as e:
        logger.warning(f"DB save interview result failed: {e}")

    logger.info(f"Interview evaluated: {data.candidate_name} → interview={interview_score}, screening={screening_score}, composite={composite_score}/100 → {shortlist_verdict}")

    # Color for email templates
    verdict_color = "#4ade80" if interview_score >= 70 else "#fbbf24" if interview_score >= 55 else "#f87171"

    # Send result email to candidate
    if data.candidate_email:
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
    # Sync from DB if local memory is empty (multi-worker)
    if not _interview_results:
        try:
            from app.storage import list_interview_results as storage_list
            db_results = await storage_list()
            for r in db_results:
                if r.get("session_id") and r["session_id"] not in _interview_results:
                    _interview_results[r["session_id"]] = r
        except Exception:
            pass
    """List all interview results."""
    return {
        "total": len(_interview_results),
        "results": [
            {
                "session_id": sid,
                "candidate_name": r.get("candidate_name", "Unknown"),
                "candidate_email": r.get("candidate_email", ""),
                "interview_score": r.get("interview_score", 0),
                "screening_score": r.get("screening_score", 0),
                "composite_score": r.get("composite_score", 0),
                "shortlisted": r.get("shortlisted", False),
                "verdict": r.get("verdict", r.get("shortlist_verdict", "pending")),
                "hr_decision": _interview_hr_decisions.get(sid, {}).get("decision", ""),
            }
            for sid, r in _interview_results.items()
        ],
    }


@router.post("/api/v1/interview/hr-decision")
async def hr_interview_decision(body: dict):
    """HR manually qualifies or rejects a candidate after AI interview."""
    session_id = body.get("session_id", "")
    decision = body.get("decision", "")  # qualify | reject
    note = body.get("note", "")

    if session_id not in _interview_results:
        return {"error": "Session not found"}

    _interview_hr_decisions[session_id] = {"decision": decision, "note": note}

    result = _interview_results[session_id]
    logger.info(f"HR interview decision: {result.get('candidate_name')} → {decision}" + (f" — {note}" if note else ""))

    # If rejected, send rejection email
    if decision == "reject" and result.get("candidate_email"):
        from app.integrations.email import send_email
        send_email(
            to=result["candidate_email"],
            subject="Application Update — Ruh AI",
            body_html=f"""<div style="font-family:-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:20px">
                <h2>Thank You, {result.get('candidate_name', 'Candidate')}</h2>
                <p>After careful evaluation, we have decided to move forward with other candidates. We wish you the best.</p>
                <p>Best regards,<br><strong>Ruh AI Hiring Team</strong></p></div>""",
        )

    return {"session_id": session_id, "decision": decision}


@router.post("/api/v1/interview/approve-shortlist")
async def approve_interview_shortlist(body: dict):
    """HR approves the shortlist — only then scheduling becomes available."""
    pipeline_id = body.get("pipeline_id", "approved")
    _interview_shortlist_approved.add(pipeline_id)
    # Persist to DB for multi-worker
    try:
        from app.storage import approve_shortlist as storage_approve_sl
        await storage_approve_sl(pipeline_id)
    except Exception:
        pass
    logger.info(f"HR approved interview shortlist for pipeline {pipeline_id}")
    return {"approved": True, "pipeline_id": pipeline_id}


@router.get("/api/v1/interview/shortlist-status")
async def shortlist_status():
    """Check if HR has approved the shortlist."""
    # Also check DB for cross-worker approvals
    result = list(_interview_shortlist_approved)
    if not result:
        try:
            from app.storage import get_approved_shortlists as storage_get_sl
            result = await storage_get_sl()
            for pid in result:
                _interview_shortlist_approved.add(pid)
        except Exception:
            pass
    return {"approved_pipelines": result}
