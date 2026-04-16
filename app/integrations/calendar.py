"""Google Calendar integration — auto-creates events with Google Meet links."""

import logging
import os
import json
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Token file path (created after first OAuth)
TOKEN_FILE = Path(__file__).parent.parent.parent / "token.json"
CREDS_FILE = Path(__file__).parent.parent.parent / "credentials.json"


def _get_calendar_service():
    """Get authenticated Google Calendar service."""
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        logger.error("Google API packages not installed. Run: pip install google-api-python-client google-auth-oauthlib")
        return None

    SCOPES = ["https://www.googleapis.com/auth/calendar"]
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDS_FILE.exists():
                logger.error(f"No credentials.json found at {CREDS_FILE}")
                return None
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
            creds = flow.run_local_server(port=9090)

        TOKEN_FILE.write_text(creds.to_json())
        logger.info("Google Calendar authenticated and token saved")

    return build("calendar", "v3", credentials=creds)


def create_interview_event(
    candidate_name: str,
    candidate_email: str,
    interviewers: list[dict],  # [{"name": "...", "email": "...", "role": "..."}]
    date: str,  # "2026-04-20"
    time: str,  # "15:00" (24h format)
    duration_minutes: int = 60,
    description: str = "",
) -> dict:
    """Create a Google Calendar event with auto-generated Google Meet link.
    
    Returns: {"meet_link": "https://meet.google.com/...", "event_id": "...", "html_link": "..."}
    """
    service = _get_calendar_service()
    if not service:
        return {"error": "Calendar service unavailable", "meet_link": ""}

    # Parse date and time
    try:
        start_dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
    except ValueError:
        try:
            start_dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %I:%M %p")
        except ValueError:
            start_dt = datetime.strptime(date, "%Y-%m-%d").replace(hour=15)

    end_dt = start_dt + timedelta(minutes=duration_minutes)

    # Build attendees list (skip empty emails)
    attendees = []
    if candidate_email and "@" in candidate_email:
        attendees.append({"email": candidate_email, "displayName": candidate_name})
    for interviewer in interviewers:
        if interviewer.get("email") and "@" in interviewer["email"]:
            attendees.append({
                "email": interviewer["email"],
                "displayName": interviewer.get("name", ""),
            })
    if not attendees:
        return {"error": "No valid email addresses for attendees", "meet_link": ""}

    event = {
        "summary": f"Technical Interview: {candidate_name} — Full Stack Developer",
        "description": f"Final technical interview for {candidate_name}\n\n{description}",
        "start": {
            "dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": "Asia/Kolkata",
        },
        "end": {
            "dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": "Asia/Kolkata",
        },
        "attendees": attendees,
        "conferenceData": {
            "createRequest": {
                "requestId": f"interview-{candidate_name.lower().replace(' ','-')}-{date}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "email", "minutes": 60},
                {"method": "popup", "minutes": 15},
            ],
        },
    }

    try:
        result = service.events().insert(
            calendarId="primary",
            body=event,
            conferenceDataVersion=1,
            sendUpdates="all",
        ).execute()

        meet_link = ""
        if result.get("conferenceData", {}).get("entryPoints"):
            for ep in result["conferenceData"]["entryPoints"]:
                if ep.get("entryPointType") == "video":
                    meet_link = ep["uri"]
                    break

        logger.info(f"Calendar event created: {result.get('htmlLink')} | Meet: {meet_link}")

        return {
            "event_id": result.get("id", ""),
            "html_link": result.get("htmlLink", ""),
            "meet_link": meet_link,
            "status": "created",
        }

    except Exception as e:
        logger.error(f"Calendar event creation failed: {e}")
        return {"error": str(e), "meet_link": ""}
