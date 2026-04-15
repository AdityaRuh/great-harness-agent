"""Email integration — sends via SMTP (Gmail App Password).

No Gmail API needed. Just an App Password from myaccount.google.com/apppasswords.
"""

import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def send_email(to: str, subject: str, body_html: str, reply_to: str = "") -> dict:
    """Send an email via SMTP."""
    smtp_user = settings.smtp_user or settings.hr_email
    smtp_pass = settings.smtp_password

    if not smtp_pass:
        logger.warning(f"No SMTP password — skipping email to {to}: {subject}")
        return {"sent": False}

    msg = MIMEMultipart("alternative")
    msg["From"] = f"Ruh AI Hiring <{smtp_user}>"
    msg["To"] = to
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to

    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, to, msg.as_string())
        logger.info(f"Email sent to {to}: {subject}")
        return {"sent": True}
    except Exception as e:
        logger.error(f"Email failed to {to}: {e}")
        return {"sent": False}
