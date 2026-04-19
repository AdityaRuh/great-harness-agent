"""Agent 4 — Final Interview Scheduling & Selection.

Sub-Agent 1: Schedule final interview (Google Meet link + calendar invites + emails)
Sub-Agent 2: Send interviewer briefing (resume analysis + tailored questions from repos)
Post-Interview: HR marks selected → congrats/rejection emails
"""

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

from app.storage import _mem_scheduled as _scheduled  # shared across workers
from app.storage import _mem_final_decisions as _final_decisions  # shared across workers


class ScheduleRequest(BaseModel):
    session_id: str
    candidate_name: str
    candidate_email: str
    interview_date: str  # "2026-04-20"
    interview_time: str  # "15:00" (24h) or "3:00 PM"
    duration_minutes: int = 60
    interviewers: list[dict]  # [{"name": "Vaibhav", "email": "vaibhav@ruh.ai", "role": "CTO"}]
    notes: str = ""


class FinalDecision(BaseModel):
    session_id: str
    decision: str  # "selected" | "rejected"
    salary: str = ""
    start_date: str = ""
    custom_message: str = ""


# ========== Sub-Agent 1: Schedule Final Interview ==========

@router.post("/api/v1/schedule-interview")
async def schedule_final_interview(data: ScheduleRequest):
    """Schedule the final human interview — sends invites to candidate + all interviewers."""

    logger.info(f"Scheduling final interview for {data.candidate_name} on {data.interview_date} {data.interview_time}")

    # Get AI interview results for context
    ai_result = _interview_results.get(data.session_id, {})
    ai_score = ai_result.get("interview_score", "N/A")
    ai_verdict = ai_result.get("verdict", "N/A")

    # Auto-create Google Calendar event with Meet link
    meet_link = ""
    try:
        from app.integrations.calendar import create_interview_event
        cal_result = create_interview_event(
            candidate_name=data.candidate_name,
            candidate_email=data.candidate_email,
            interviewers=data.interviewers,
            date=data.interview_date,
            time=data.interview_time,
            duration_minutes=data.duration_minutes,
            description=f"AI Screening Score: {ai_score}/100 — {ai_verdict}",
        )
        meet_link = cal_result.get("meet_link", "")
        if meet_link:
            logger.info(f"Google Meet auto-created: {meet_link}")
        else:
            logger.warning(f"Could not create Meet link: {cal_result.get('error', 'unknown')}")
            meet_link = "Google Meet link will be shared separately"
    except Exception as e:
        logger.warning(f"Calendar integration failed: {e}")
        meet_link = "Google Meet link will be shared separately"

    # 1. Send invite to candidate
    candidate_body = f"""
    <div style="font-family:-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:20px">
        <h2 style="color:#1a1a2e">Final Interview — Full Stack Developer at Ruh AI</h2>
        <p>Hi {data.candidate_name},</p>
        <p>Congratulations on clearing the screening round! You've been shortlisted for a <strong>final technical interview</strong> with our team.</p>

        <div style="background:#f0f0ff;padding:16px;border-radius:8px;margin:16px 0;border-left:4px solid #7c6ef0">
            <p style="margin:4px 0"><strong>Date:</strong> {data.interview_date}</p>
            <p style="margin:4px 0"><strong>Time:</strong> {data.interview_time}</p>
            <p style="margin:4px 0"><strong>Duration:</strong> {data.duration_minutes} minutes</p>
            <p style="margin:4px 0"><strong>Meeting Link:</strong> <a href="{meet_link}">{meet_link}</a></p>
            <p style="margin:4px 0"><strong>Interviewers:</strong> {', '.join(i['name'] + ' (' + i.get('role','') + ')' for i in data.interviewers)}</p>
        </div>

        <h3 style="color:#555;font-size:14px">How to prepare:</h3>
        <ul style="color:#666;font-size:14px">
            <li>Review the projects mentioned in your resume — expect deep-dive questions</li>
            <li>Be ready to discuss system design and architecture decisions</li>
            <li>Have your development environment ready for a possible live coding exercise</li>
            <li>Prepare questions about the team and role</li>
        </ul>

        {f'<p style="color:#666;font-size:13px"><em>{data.notes}</em></p>' if data.notes else ''}

        <p>Best of luck!<br><strong>Ruh AI Hiring Team</strong></p>
    </div>
    """
    send_email(data.candidate_email, f"Final Interview — Full Stack Developer | {data.interview_date} {data.interview_time}", candidate_body)
    logger.info(f"Invite sent to candidate: {data.candidate_email}")

    # 2. Generate briefing + questions for each interviewer (Sub-Agent 2)
    for interviewer in data.interviewers:
        briefing = await _generate_interviewer_briefing(
            candidate_name=data.candidate_name,
            interviewer_name=interviewer["name"],
            interviewer_role=interviewer.get("role", "Interviewer"),
            ai_result=ai_result,
        )

        interviewer_body = f"""
        <div style="font-family:-apple-system,sans-serif;max-width:700px;margin:0 auto;padding:20px">
            <h2 style="color:#1a1a2e">Interview Briefing: {data.candidate_name}</h2>
            <p>Hi {interviewer['name']},</p>
            <p>You have a <strong>final technical interview</strong> scheduled:</p>

            <div style="background:#f0f0ff;padding:16px;border-radius:8px;margin:16px 0;border-left:4px solid #7c6ef0">
                <p style="margin:4px 0"><strong>Candidate:</strong> {data.candidate_name}</p>
                <p style="margin:4px 0"><strong>Date:</strong> {data.interview_date} at {data.interview_time}</p>
                <p style="margin:4px 0"><strong>Duration:</strong> {data.duration_minutes} min</p>
                <p style="margin:4px 0"><strong>Meeting:</strong> <a href="{meet_link}">{meet_link}</a></p>
                <p style="margin:4px 0"><strong>AI Screening Score:</strong> {ai_score}/100 — {ai_verdict}</p>
            </div>

            <h3 style="color:#7c6ef0;margin-top:24px">Candidate Briefing & Suggested Questions</h3>
            <div style="background:#fafafa;padding:16px;border-radius:8px;white-space:pre-wrap;line-height:1.7;font-size:14px">{briefing}</div>

            <p style="margin-top:20px;color:#888;font-size:13px">After the interview, please reply to this email or update the dashboard with your verdict (Selected / Not Selected) and any feedback.</p>

            <p><a href="{settings.app_url}/dashboard" style="background:#7c6ef0;color:white;padding:10px 24px;border-radius:8px;text-decoration:none;font-weight:600">Open Dashboard</a></p>
        </div>
        """
        send_email(
            interviewer["email"],
            f"Interview Briefing: {data.candidate_name} — Full Stack Developer | {data.interview_date}",
            interviewer_body,
        )
        logger.info(f"Briefing sent to interviewer: {interviewer['name']} ({interviewer['email']})")

    # Store schedule
    _scheduled[data.session_id] = {
        "candidate_name": data.candidate_name,
        "candidate_email": data.candidate_email,
        "date": data.interview_date,
        "time": data.interview_time,
        "meet_link": meet_link,
        "interviewers": data.interviewers,
        "status": "scheduled",
    }

    return {
        "status": "scheduled",
        "candidate": data.candidate_name,
        "date": f"{data.interview_date} {data.interview_time}",
        "meet_link": meet_link,
        "invites_sent": len(data.interviewers) + 1,
    }


# ========== Sub-Agent 2: Generate Interviewer Briefing ==========

async def _generate_interviewer_briefing(
    candidate_name: str,
    interviewer_name: str,
    interviewer_role: str,
    ai_result: dict,
) -> str:
    """Generate a detailed briefing for the interviewer with resume analysis + tailored questions."""

    evaluation = ai_result.get("evaluation", {})
    transcript = ai_result.get("transcript", [])

    # Candidate answers from AI interview
    answers = [m["text"] for m in transcript if m.get("speaker") in ("You", "Candidate")]
    answers_summary = "\n".join(f"- {a}" for a in answers[:8])

    prompt = f"""You are preparing a briefing for {interviewer_name} ({interviewer_role}) who will conduct a final technical interview with {candidate_name} for a Full Stack Developer role at Ruh AI.

=== AI SCREENING RESULTS ===
Score: {ai_result.get('interview_score', 'N/A')}/100
Verdict: {ai_result.get('verdict', 'N/A')}
Strengths: {evaluation.get('top_strengths', [])}
Concerns: {evaluation.get('concerns', [])}
Recommendation: {evaluation.get('recommendation', 'N/A')}

=== CANDIDATE'S KEY ANSWERS FROM AI INTERVIEW ===
{answers_summary}

=== GENERATE BRIEFING ===

Write a briefing with these sections:

1. CANDIDATE SNAPSHOT (3-4 sentences)
   Quick overview — who they are, experience level, tech stack, standout signals.

2. RESUME ANALYSIS HIGHLIGHTS
   - Strong areas (3 bullets)
   - Areas needing verification (3 bullets)
   - Red flags if any (1-2 bullets)

3. SUGGESTED INTERVIEW QUESTIONS (10 questions)
   Focus on:
   - Deep-dive into their actual projects (ask specifics about architecture decisions)
   - Probe gaps identified in screening (experience timeline, no public repos)
   - Live coding: ask them to build something small with their claimed stack
   - System design: "How would you design X" using technologies from their resume
   - Questions about their repos/projects that test real understanding vs surface knowledge

   Format each question as:
   Q1: [Question text]
   What to look for: [What a good answer includes]

4. EVALUATION CRITERIA
   After the interview, rate the candidate on: Technical Depth, Problem Solving, Communication, Culture Fit (each 1-5).

Keep it concise, actionable, and specific to this candidate. The interviewer should walk in fully prepared."""

    try:
        return call_llm(prompt, max_tokens=4096)
    except Exception as e:
        logger.error(f"Briefing generation failed: {e}")
        return f"Briefing generation failed: {e}\n\nCandidate: {candidate_name}\nAI Score: {ai_result.get('interview_score', 'N/A')}/100"


# ========== Post-Interview: Final Decision ==========

@router.post("/api/v1/final-decision")
async def final_decision(data: FinalDecision):
    """After human interview — HR marks selected or rejected. Sends appropriate email."""
    scheduled = _scheduled.get(data.session_id)
    ai_result = _interview_results.get(data.session_id, {})

    candidate_name = data.session_id
    candidate_email = ""
    if scheduled:
        candidate_name = scheduled["candidate_name"]
        candidate_email = scheduled["candidate_email"]
    elif ai_result:
        candidate_name = ai_result.get("candidate_name", data.session_id)
        candidate_email = ai_result.get("candidate_email", "")

    if not candidate_email:
        return {"error": "No candidate email found"}

    if data.decision == "selected":
        subject = f"Congratulations! Offer — Full Stack Developer at Ruh AI"
        body = f"""
        <div style="font-family:-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:20px">
            <h2 style="color:#1a1a2e">Congratulations, {candidate_name}! 🎉</h2>
            <p>We are thrilled to offer you the position of <strong>Full Stack Developer</strong> at Ruh AI.</p>
            <p>After a thorough evaluation process — resume screening, AI interview, and final technical interview — our team was impressed by your skills, depth of experience, and alignment with our mission.</p>
            {f'<div style="background:#f0fff4;padding:16px;border-radius:8px;margin:16px 0;border-left:4px solid #4ade80"><p style="margin:4px 0"><strong>Compensation:</strong> {data.salary}</p>' if data.salary else '<div style="display:none">'}
            {f'<p style="margin:4px 0"><strong>Proposed Start Date:</strong> {data.start_date}</p></div>' if data.start_date else '</div>'}
            {f'<p>{data.custom_message}</p>' if data.custom_message else ''}
            <p>Please reply to this email within 5 business days to confirm your acceptance or to discuss any questions.</p>
            <p>We look forward to having you on the team!<br><strong>Ruh AI Hiring Team</strong></p>
        </div>
        """
        logger.info(f"Sending OFFER to {candidate_name} ({candidate_email})")
    else:
        subject = f"Application Update — Ruh AI"
        body = f"""
        <div style="font-family:-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:20px">
            <h2 style="color:#1a1a2e">Thank You, {candidate_name}</h2>
            <p>Thank you for your time and effort throughout our interview process for the <strong>Full Stack Developer</strong> position.</p>
            <p>After careful consideration, we have decided to move forward with another candidate whose profile more closely matches our current requirements.</p>
            {f'<p>{data.custom_message}</p>' if data.custom_message else ''}
            <p>Your technical skills are strong and we encourage you to apply for future openings at Ruh AI.</p>
            <p>Wishing you the best,<br><strong>Ruh AI Hiring Team</strong></p>
        </div>
        """
        logger.info(f"Sending REJECTION to {candidate_name} ({candidate_email})")

    result = send_email(candidate_email, subject, body)

    # Also notify HR
    hr_body = f"""
    <div style="font-family:-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:20px">
        <h2>Final Decision: {candidate_name}</h2>
        <p><strong>Decision:</strong> {'✅ SELECTED — Offer Sent' if data.decision == 'selected' else '❌ REJECTED — Notification Sent'}</p>
        {f'<p><strong>Salary:</strong> {data.salary}</p>' if data.salary else ''}
        <p><strong>Email sent:</strong> {'Yes' if result.get('sent') else 'Failed'}</p>
    </div>
    """
    send_email(settings.hr_email, f"Final Decision: {candidate_name} — {'SELECTED' if data.decision == 'selected' else 'REJECTED'}", hr_body)

    _final_decisions[data.session_id] = {
        "candidate_name": candidate_name,
        "decision": data.decision,
        "email_sent": result.get("sent", False),
    }

    return {
        "candidate": candidate_name,
        "decision": data.decision,
        "email_sent": result.get("sent", False),
    }


@router.get("/api/v1/scheduled-interviews")
async def list_scheduled():
    interviews = []
    for sid, data in _scheduled.items():
        interviews.append({**data, "session_id": sid})
    return {"total": len(_scheduled), "interviews": interviews}


@router.get("/api/v1/final-decisions")
async def list_decisions():
    return {"total": len(_final_decisions), "decisions": list(_final_decisions.values())}
