"""Checkpoint timeout reminders — nudges HR when checkpoints sit idle.

Uses APScheduler to schedule reminder emails:
- 4 hours after checkpoint → first reminder
- 24 hours → second reminder (escalation)
- 72 hours → auto-timeout warning

No Celery. No Redis. Just a simple in-process scheduler.
"""

import logging
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from app.integrations.email import send_email
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Singleton scheduler
_scheduler: AsyncIOScheduler | None = None

# Reminder intervals
FIRST_REMINDER_HOURS = 4
SECOND_REMINDER_HOURS = 24
TIMEOUT_WARNING_HOURS = 72


def get_scheduler() -> AsyncIOScheduler:
    """Get or create the scheduler singleton."""
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


def start_scheduler():
    """Start the background scheduler."""
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
        logger.info("APScheduler started for checkpoint reminders")


def stop_scheduler():
    """Stop the scheduler gracefully."""
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")


def schedule_checkpoint_reminders(
    pipeline_id: str,
    checkpoint_name: str,
    role_title: str,
    context: str = "",
):
    """Schedule reminder emails for a checkpoint.

    Called automatically when a checkpoint is reached.
    Reminders auto-cancel when the checkpoint is resolved.
    """
    scheduler = get_scheduler()
    hr_email = settings.hr_email
    if not hr_email:
        logger.warning("No HR_EMAIL configured — checkpoint reminders disabled")
        return

    now = datetime.now(timezone.utc)
    base_id = f"reminder_{pipeline_id}_{checkpoint_name}"

    # First reminder (4 hours)
    scheduler.add_job(
        _send_reminder,
        trigger=DateTrigger(run_date=now + timedelta(hours=FIRST_REMINDER_HOURS)),
        id=f"{base_id}_1st",
        replace_existing=True,
        kwargs={
            "to": hr_email,
            "pipeline_id": pipeline_id,
            "checkpoint": checkpoint_name,
            "role_title": role_title,
            "urgency": "gentle",
            "context": context,
        },
    )

    # Second reminder (24 hours)
    scheduler.add_job(
        _send_reminder,
        trigger=DateTrigger(run_date=now + timedelta(hours=SECOND_REMINDER_HOURS)),
        id=f"{base_id}_2nd",
        replace_existing=True,
        kwargs={
            "to": hr_email,
            "pipeline_id": pipeline_id,
            "checkpoint": checkpoint_name,
            "role_title": role_title,
            "urgency": "escalation",
            "context": context,
        },
    )

    # Timeout warning (72 hours)
    scheduler.add_job(
        _send_reminder,
        trigger=DateTrigger(run_date=now + timedelta(hours=TIMEOUT_WARNING_HOURS)),
        id=f"{base_id}_timeout",
        replace_existing=True,
        kwargs={
            "to": hr_email,
            "pipeline_id": pipeline_id,
            "checkpoint": checkpoint_name,
            "role_title": role_title,
            "urgency": "timeout",
            "context": context,
        },
    )

    logger.info(
        f"Scheduled reminders for {checkpoint_name} (pipeline {pipeline_id[:8]}): "
        f"{FIRST_REMINDER_HOURS}h, {SECOND_REMINDER_HOURS}h, {TIMEOUT_WARNING_HOURS}h"
    )


def cancel_checkpoint_reminders(pipeline_id: str, checkpoint_name: str):
    """Cancel all reminders for a resolved checkpoint."""
    scheduler = get_scheduler()
    base_id = f"reminder_{pipeline_id}_{checkpoint_name}"

    for suffix in ["_1st", "_2nd", "_timeout"]:
        job_id = f"{base_id}{suffix}"
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass  # Job may have already fired or not exist

    logger.info(f"Cancelled reminders for {checkpoint_name} (pipeline {pipeline_id[:8]})")


def _send_reminder(
    to: str,
    pipeline_id: str,
    checkpoint: str,
    role_title: str,
    urgency: str,
    context: str,
):
    """Send a checkpoint reminder email."""
    checkpoint_labels = {
        "jd_approval": "JD Approval",
        "ranking_review": "Candidate Ranking Review",
        "shortlist_confirmation": "Interview Shortlist Confirmation",
        "final_verdict": "Final Interview Verdict",
    }
    cp_label = checkpoint_labels.get(checkpoint, checkpoint)

    if urgency == "gentle":
        subject = f"Reminder: {cp_label} pending — {role_title}"
        tone = "This is a friendly reminder that your review is pending."
    elif urgency == "escalation":
        subject = f"Action needed: {cp_label} waiting 24h — {role_title}"
        tone = "This checkpoint has been waiting for <strong>24 hours</strong>. The hiring pipeline is paused until you respond."
    else:  # timeout
        subject = f"⚠️ Pipeline stalled: {cp_label} waiting 72h — {role_title}"
        tone = "This checkpoint has been <strong>waiting 72 hours</strong>. The pipeline may timeout soon. Please respond urgently."

    body = f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 600px;">
        <h2 style="color: #1a1a2e;">{subject}</h2>
        <p>{tone}</p>
        <p><strong>Checkpoint:</strong> {cp_label}<br>
        <strong>Pipeline:</strong> {role_title} ({pipeline_id[:8]})</p>
        {f'<p><strong>Context:</strong> {context}</p>' if context else ''}
        <p style="text-align: center; margin: 20px 0;">
            <a href="{settings.app_url}/api/v1/pipelines/{pipeline_id}"
               style="background: #7c6ef0; color: white; padding: 12px 28px;
                      border-radius: 8px; text-decoration: none; font-weight: 600;">
                Review Now
            </a>
        </p>
    </div>
    """

    send_email(to=to, subject=subject, body_html=body)
    logger.info(f"Sent {urgency} reminder for {checkpoint} (pipeline {pipeline_id[:8]})")
