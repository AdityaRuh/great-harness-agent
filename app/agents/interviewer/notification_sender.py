"""Notification sender — shortlist/rejection emails after avatar interview."""

import logging
from app.integrations.email import send_email

logger = logging.getLogger(__name__)


def send_shortlist_notifications(
    interview_results: list[dict],
    role_title: str,
    threshold: float = 50.0,
) -> dict:
    """Send shortlist or rejection emails based on interview scores."""
    shortlisted = []
    rejected = []

    for result in interview_results:
        name = result.get("candidate_name", "Candidate")
        email = result.get("email")
        score = result.get("total_score", 0)

        if not email:
            continue

        if score >= threshold:
            send_email(
                to=email,
                subject=f"Great news! You've been shortlisted — {role_title}",
                body_html=f"""
                <p>Hi {name},</p>
                <p>We're pleased to let you know that you've been <strong>shortlisted</strong>
                for the {role_title} position!</p>
                <p>Our team will reach out shortly to schedule the next round — a conversation
                with our hiring panel.</p>
                <p>Best,<br>Hiring Team</p>
                """,
            )
            shortlisted.append(name)
        else:
            send_email(
                to=email,
                subject=f"Update on your application — {role_title}",
                body_html=f"""
                <p>Hi {name},</p>
                <p>Thank you for taking the time to interview for the {role_title} position.</p>
                <p>After careful review, we've decided to move forward with other candidates
                whose experience more closely matches our current needs.</p>
                <p>We appreciate your interest and encourage you to apply again in the future.</p>
                <p>Best wishes,<br>Hiring Team</p>
                """,
            )
            rejected.append(name)

    logger.info(f"Notifications sent: {len(shortlisted)} shortlisted, {len(rejected)} rejected")
    return {"shortlisted": shortlisted, "rejected": rejected}
