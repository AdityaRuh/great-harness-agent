"""Invite sender — emails interview links to shortlisted candidates."""

import logging
import uuid
from app.integrations.email import send_email
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def send_interview_invites(
    ranked_candidates: list[dict],
    pipeline_id: str,
    role_title: str,
    max_invites: int = 30,
) -> list[dict]:
    """Send interview invites to top-ranked candidates.

    Creates a unique session URL for each candidate.
    """
    invited = []

    for candidate in ranked_candidates[:max_invites]:
        profile = candidate.get("candidate", {})
        name = profile.get("name", "Candidate")
        email = profile.get("email")

        if not email:
            logger.warning(f"No email for {name} — skipping invite")
            continue

        session_id = str(uuid.uuid4())
        interview_url = f"{settings.app_url}/interview/{session_id}"

        body = f"""
        <div style="font-family: -apple-system, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #1a1a2e;">Interview Invitation — {role_title}</h2>

            <p>Hi {name},</p>

            <p>Congratulations on being shortlisted for the <strong>{role_title}</strong> position!</p>

            <p>Your AI interview is ready. Click below to start:</p>

            <p style="text-align: center; margin: 24px 0;">
                <a href="{interview_url}"
                   style="background: #7c6ef0; color: white; padding: 14px 32px;
                          border-radius: 8px; text-decoration: none; font-weight: 600;">
                    Start Your Interview
                </a>
            </p>

            <p>The interview takes <strong>15-25 minutes</strong>. You'll speak with an AI interviewer
            who asks technical questions based on your resume and our requirements.</p>

            <h3 style="color: #555; font-size: 14px;">Tips for a smooth experience:</h3>
            <ul style="color: #666; font-size: 14px;">
                <li>Use Chrome or Edge (WebGL required for the 3D avatar)</li>
                <li>Find a quiet environment with a working microphone</li>
                <li>Speak naturally — the interviewer adapts to your responses</li>
                <li>Take your time with each answer</li>
            </ul>

            <p style="color: #999; font-size: 13px;">This link expires in 7 days.</p>

            <p>Best of luck!<br>
            <strong>Hiring Team</strong></p>
        </div>
        """

        result = send_email(
            to=email,
            subject=f"Interview Invitation — {role_title}",
            body_html=body,
        )

        invited.append({
            "candidate_name": name,
            "email": email,
            "session_id": session_id,
            "interview_url": interview_url,
            "email_sent": result.get("sent", False),
            "screening_score": candidate.get("screening_result", {}).get("total_score"),
            "tier": candidate.get("tier"),
            "rank": candidate.get("final_rank"),
        })

    logger.info(f"Sent {len(invited)} interview invites for {role_title}")
    return invited
