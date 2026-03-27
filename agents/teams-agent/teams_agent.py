"""
Teams Agent — refactored for server-side execution.

Adapted from the original assistant_agent.py (Teams_msg_call_instant).
- Removed: CLI loop, subprocess.run, webbrowser.open, interactive input
- Changed: Returns structured JSON instead of opening URLs
- Changed: Accepts task data as input, returns result as output
- Kept: Ollama intent parsing, Microsoft Graph contact search
"""

import json
import os
import re
import urllib.parse
import threading
from datetime import datetime, timedelta
from dataclasses import dataclass, field

import msal
import requests

# ---------------------------------------------------------------------------
# Configuration (from environment variables)
# ---------------------------------------------------------------------------

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Twilio configuration
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
WHATSAPP_TO_NUMBER = os.getenv("WHATSAPP_TO_NUMBER", "")
TWILIO_CALL_FROM = os.getenv("TWILIO_CALL_FROM", "+18127953318")
CALL_TO_NUMBER = os.getenv("CALL_TO_NUMBER", "")
TWILIO_SMS_FROM = os.getenv("TWILIO_SMS_FROM", "+18127953318")
SMS_TO_NUMBER = os.getenv("SMS_TO_NUMBER", "")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ParsedTurn:
    intent: str
    contact_query: str | None
    message: str | None
    confirmed: bool
    cancelled: bool


# ---------------------------------------------------------------------------
# Microsoft Graph client
# ---------------------------------------------------------------------------

from typing import Any, Dict

from graph_client import GraphClient, DeviceFlowRequired, auth_store

class GraphDirectoryClient(GraphClient):
    """Searches Microsoft Teams contacts via the Graph API."""

    def __init__(self) -> None:
        super().__init__()


    def search_people(self, query: str) -> list[dict]:
        query = query.strip()
        if not query:
            return []

        if _is_valid_email(query):
            return [{"displayName": query, "email": query}]

        results: list[dict] = []
        results.extend(self._search_me_people(query))
        results.extend(self._search_users(query))
        return _rank_and_deduplicate(query, results)

    def _search_me_people(self, query: str) -> list[dict]:
        try:
            payload = self.get(
                "/me/people",
                params={"$search": f'"{query}"', "$top": "10"},
            )
        except requests.HTTPError:
            return []

        contacts = []
        for person in payload.get("value", []):
            email = None
            scored = person.get("scoredEmailAddresses") or []
            if scored:
                email = scored[0].get("address")
            email = email or person.get("userPrincipalName") or person.get("mail")
            if email:
                contacts.append({"displayName": person.get("displayName") or email, "email": email})
        return contacts

    def _search_users(self, query: str) -> list[dict]:
        escaped = query.replace("'", "''")
        filter_query = (
            f"startswith(displayName,'{escaped}') "
            f"or startswith(mail,'{escaped}') "
            f"or startswith(userPrincipalName,'{escaped}')"
        )

        try:
            payload = self.get(
                "/users",
                params={"$filter": filter_query, "$select": "displayName,mail,userPrincipalName", "$top": "10"},
            )
        except requests.HTTPError:
            return []

        contacts = []
        for user in payload.get("value", []):
            email = user.get("mail") or user.get("userPrincipalName")
            if email:
                contacts.append({"displayName": user.get("displayName") or email, "email": email})
        return contacts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_valid_email(value: str | None) -> bool:
    return bool(value and EMAIL_PATTERN.match(value.strip()))


def _rank_and_deduplicate(query: str, contacts: list[dict]) -> list[dict]:
    query_lower = query.lower()
    words = [w for w in query_lower.split() if w]
    ranked, seen = [], set()

    for contact in contacts:
        email = (contact.get("email") or "").strip().lower()
        name = (contact.get("displayName") or "").strip()
        if not email or email in seen:
            continue

        name_lower = name.lower()
        score = 0
        if name_lower == query_lower:
            score += 100
        if email == query_lower:
            score += 100
        if name_lower.startswith(query_lower):
            score += 70
        if query_lower in name_lower:
            score += 40
        if query_lower in email:
            score += 30
        score += sum(10 for w in words if w in name_lower)

        seen.add(email)
        ranked.append((score, {"displayName": name or email, "email": email}))

    ranked.sort(key=lambda item: (-item[0], item[1]["displayName"]))
    return [c for _, c in ranked]


def _build_teams_call_url(email: str) -> str:
    return f"msteams://teams.microsoft.com/l/call/0/0?users={email}"


def _build_teams_message_url(email: str, message: str) -> str:
    encoded = urllib.parse.quote(message)
    return f"msteams://teams.microsoft.com/l/chat/0/0?users={email}&message={encoded}"


def _build_teams_meeting_url(title: str, attendees: list[str], description: str, start_dt_str: str, end_dt_str: str) -> str:
    """Build a Microsoft Teams deep-link URL to pre-fill a meeting."""
    subject = urllib.parse.quote(title or "Team Meeting")
    attendees_str = urllib.parse.quote(",".join(attendees))
    content = urllib.parse.quote(description or "")
    # Format for Teams: YYYY-MM-DDTHH:mm:ss
    start = urllib.parse.quote(start_dt_str)
    end = urllib.parse.quote(end_dt_str)

    return (
        f"https://teams.microsoft.com/l/meeting/new?"
        f"subject={subject}"
        f"&attendees={attendees_str}"
        f"&content={content}"
        f"&startTime={start}"
        f"&endTime={end}"
    )


def _build_outlook_meeting_url(title: str, attendees: list[str], description: str, start_dt_str: str, end_dt_str: str) -> str:
    """Fallback: Outlook Web new event with Teams meeting enabled."""
    subject = urllib.parse.quote(title or "Team Meeting")
    attendees_str = urllib.parse.quote(";".join(attendees))
    body = urllib.parse.quote(description or "")
    start = urllib.parse.quote(start_dt_str)
    end = urllib.parse.quote(end_dt_str)

    return (
        f"https://outlook.office.com/calendar/action/compose?"
        f"subject={subject}&to={attendees_str}&body={body}"
        f"&startdt={start}&enddt={end}&isonlinemeeting=true"
    )


# ---------------------------------------------------------------------------
# Main agent function — called by the FastAPI server
# ---------------------------------------------------------------------------

def run_teams_action(task_data: dict) -> dict:
    """
    Execute a Teams agent action.

    Input (task_data):
        {
            # For make_call / send_message:
            "action": "make_call" | "send_message" | "schedule_meeting",
            "contact": "person name or email",
            "message": "optional message text",
            # For schedule_meeting:
            "title": "Meeting Title",
            "attendees": ["Name", "email@corp.com"],
            "date": "YYYY-MM-DD",
            "time": "HH:MM",
            "duration": 60,
            "description": "agenda"
        }

    Output:
        {
            "status": "success" | "failed",
            "type": "teams_call" | "teams_message",
            "url": "msteams://...",
            "displayName": "...",
            "email": "...",
            "error": "..." (only on failure)
        }
    """
    action = task_data.get("action", "")

    try:
        # Route schedule_meeting before contact validation (it uses different params)
        if action == "schedule_meeting":
            return _handle_schedule_meeting(task_data)
    except DeviceFlowRequired as e:
        return {
            "status": "action_required",
            "type": "device_auth",
            "flow": e.flow_data,
        }

    contact_query = task_data.get("contact", "")
    message_text = task_data.get("message", "")

    if not contact_query:
        return {"status": "failed", "error": "No contact specified."}

    if action not in ("make_call", "send_message"):
        return {"status": "failed", "error": f"Unknown action: {action}"}

    if action == "send_message" and not message_text:
        return {"status": "failed", "error": "No message text provided for send_message action."}

    # ── Resolve contact ──────────────────────────────────────────────────
    try:
        graph = GraphDirectoryClient()
        matches = graph.search_people(contact_query)
    except DeviceFlowRequired as e:
        return {
            "status": "action_required",
            "type": "device_auth",
            "flow": e.flow_data,
        }
    except Exception as exc:
        # If Graph is not configured, fall back to using the query as-is
        # (assumes user provided an email or we just use it verbatim)
        if _is_valid_email(contact_query):
            matches = [{"displayName": contact_query, "email": contact_query}]
        else:
            return {
                "status": "failed",
                "error": f"Microsoft Graph lookup failed: {exc}. "
                         f"Ensure GRAPH_CLIENT_ID and GRAPH_TENANT_ID are set.",
            }

    if not matches:
        return {
            "status": "failed",
            "error": f'No Teams contact found matching "{contact_query}".',
        }

    # Use the best match (first result)
    contact = matches[0]
    email = contact["email"]
    display_name = contact["displayName"]

    # ── Build result ─────────────────────────────────────────────────────
    if action == "make_call":
        return {
            "status": "success",
            "type": "teams_call",
            "url": _build_teams_call_url(email),
            "displayName": display_name,
            "email": email,
        }

    if action == "send_message":
        return {
            "status": "success",
            "type": "teams_message",
            "url": _build_teams_message_url(email, message_text),
            "displayName": display_name,
            "email": email,
        }

    return {"status": "failed", "error": "Unexpected state."}


def _handle_schedule_meeting(data: dict) -> dict:
    """Port of assistant.py logic replacing PowerShell with Graph API."""
    title = data.get("title", "Team Meeting")
    attendees_raw = data.get("attendees", [])
    if isinstance(attendees_raw, str):
        attendees_raw = [attendees_raw]
    
    date_str = data.get("date", "")
    time_str = data.get("time", "")
    duration = int(data.get("duration", 30))
    description = data.get("message") or data.get("description", "")

    if not date_str or not time_str:
        return {"status": "failed", "error": "Missing date or time for meeting."}

    # ── Resolve Attendees ────────────────────────────────────────────────
    graph = GraphDirectoryClient()
    resolved_emails = []
    resolved_details = []
    unresolved = []

    for name in attendees_raw:
        if _is_valid_email(name):
            email = name.strip()
            resolved_emails.append(email)
            resolved_details.append({"name": email, "email": email})
            continue
        
        try:
            matches = graph.search_people(name)
            if matches:
                # Take best match
                match = matches[0]
                resolved_emails.append(match["email"])
                resolved_details.append({
                    "name": match["displayName"],
                    "email": match["email"]
                })
            else:
                unresolved.append(name)
        except Exception:
            unresolved.append(name)

    # ── Calculate Timestamps ─────────────────────────────────────────────
    # Format: YYYY-MM-DDTHH:mm:ss
    try:
        from datetime import datetime, timedelta
        start_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(minutes=duration)
        
        start_ts = start_dt.strftime("%Y-%m-%dT%H:%M:%S")
        end_ts = end_dt.strftime("%Y-%m-%dT%H:%M:%S")
    except Exception as exc:
        return {"status": "failed", "error": f"Invalid date/time format: {exc}"}

    # ── Build URLs ───────────────────────────────────────────────────────
    teams_url = _build_teams_meeting_url(title, resolved_emails, description, start_ts, end_ts)
    outlook_url = _build_outlook_meeting_url(title, resolved_emails, description, start_ts, end_ts)

    # ── Trigger Notifications ─────────────────────────────────────────────
    pref = data.get("notification_preference", "none").lower()
    if pref and pref != "none":
        meeting_data_for_notif = {
            "title": title,
            "date": date_str,
            "time": time_str,
            "duration": duration,
            "attendee_labels": [f"{r['name']} <{r['email']}>" for r in resolved_details]
        }
        dispatch_notifications(meeting_data_for_notif, teams_url, pref)

    return {
        "status": "success",
        "type": "teams_meeting",
        "teamsUrl": teams_url,
        "outlookUrl": outlook_url,
        "title": title,
        "date": date_str,
        "time": time_str,
        "duration": duration,
        "resolvedAttendees": resolved_details,
        "unresolvedAttendees": unresolved,
        "description": description
    }


# ---------------------------------------------------------------------------
# Twilio Notification Helpers
# ---------------------------------------------------------------------------

def get_meeting_window(meeting: dict) -> tuple[datetime, datetime]:
    start_dt = datetime.strptime(f"{meeting['date']} {meeting['time']}", "%Y-%m-%d %H:%M")
    end_dt = start_dt + timedelta(minutes=int(meeting.get("duration", 60)))
    return start_dt, end_dt

def send_whatsapp_message(to_number: str, body: str) -> None:
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN: return
    import base64; from urllib.parse import urlencode; from urllib import request, error
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    credentials = base64.b64encode(f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode()).decode()
    payload = urlencode({
        "From": TWILIO_WHATSAPP_FROM,
        "To": f"whatsapp:{to_number}",
        "Body": body,
    }).encode("utf-8")
    req = request.Request(url, data=payload, method="POST", headers={
        "Authorization": f"Basic {credentials}",
        "Content-Type": "application/x-www-form-urlencoded",
    })
    try:
        with request.urlopen(req, timeout=30) as resp: pass
    except error.URLError: pass

def schedule_whatsapp_reminder(meeting: dict, to_number: str, teams_url: str = "") -> None:
    if not to_number: return
    start_dt, _ = get_meeting_window(meeting)
    delay_seconds = (start_dt - timedelta(minutes=1) - datetime.now()).total_seconds()
    if delay_seconds <= 0: delay_seconds = 0
    body = (f"⏰ Reminder: '{meeting.get('title','your meeting')}' starts in 1 minute!\n"
            f"📅 {start_dt.strftime('%A, %B %d, %Y')} at {start_dt.strftime('%I:%M %p')}\n")
    if teams_url: body += f"🔗 Join now:\n{teams_url}"
    timer = threading.Timer(delay_seconds, lambda: send_whatsapp_message(to_number, body))
    timer.daemon = True; timer.start()

def make_ivr_call(to_number: str, twiml_message: str) -> None:
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN: return
    import base64; from urllib.parse import urlencode; from urllib import request, error
    twiml = (f"<Response><Say voice='alice' language='en-IN'>{twiml_message}</Say>"
             f"<Pause length='1'/><Say voice='alice' language='en-IN'>{twiml_message}</Say></Response>")
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Calls.json"
    credentials = base64.b64encode(f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode()).decode()
    payload = urlencode({"From": TWILIO_CALL_FROM, "To": to_number, "Twiml": twiml}).encode("utf-8")
    req = request.Request(url, data=payload, method="POST", headers={
        "Authorization": f"Basic {credentials}",
        "Content-Type": "application/x-www-form-urlencoded",
    })
    try:
        with request.urlopen(req, timeout=30) as resp: pass
    except error.URLError: pass

def schedule_call_reminder(meeting: dict, to_number: str) -> None:
    if not to_number: return
    start_dt, _ = get_meeting_window(meeting)
    delay_seconds = (start_dt - timedelta(minutes=1) - datetime.now()).total_seconds()
    if delay_seconds <= 0: delay_seconds = 0
    message = (f"Reminder! Your Teams meeting '{meeting.get('title', 'your meeting')}' starts in 1 minute at {start_dt.strftime('%I:%M %p')}. "
               f"Please join now using the link sent to your WhatsApp. Thank you.")
    timer = threading.Timer(delay_seconds, lambda: make_ivr_call(to_number, message))
    timer.daemon = True; timer.start()

def send_sms(to_number: str, body: str) -> None:
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN: return
    import base64; from urllib.parse import urlencode; from urllib import request, error
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    credentials = base64.b64encode(f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode()).decode()
    payload = urlencode({"From": TWILIO_SMS_FROM, "To": to_number, "Body": body}).encode("utf-8")
    req = request.Request(url, data=payload, method="POST", headers={
        "Authorization": f"Basic {credentials}",
        "Content-Type": "application/x-www-form-urlencoded",
    })
    try:
        with request.urlopen(req, timeout=30) as resp: pass
    except error.URLError: pass

def schedule_sms_reminder(meeting: dict, to_number: str, teams_url: str = "") -> None:
    if not to_number: return
    start_dt, _ = get_meeting_window(meeting)
    delay_seconds = (start_dt - timedelta(minutes=1) - datetime.now()).total_seconds()
    if delay_seconds <= 0: delay_seconds = 0
    body = f"Reminder: '{meeting.get('title','meeting')}' starts in 1 minute at {start_dt.strftime('%I:%M %p')}.\n"
    if teams_url: body += f"Join: {teams_url}"
    timer = threading.Timer(delay_seconds, lambda: send_sms(to_number, body))
    timer.daemon = True; timer.start()

def dispatch_notifications(meeting: dict, teams_url: str, preference: str) -> None:
    start_dt, _ = get_meeting_window(meeting)
    do_whatsapp = preference in ("whatsapp", "all")
    do_call     = preference in ("call", "all")
    do_sms      = preference in ("sms", "all")

    if do_whatsapp and WHATSAPP_TO_NUMBER:
        body = (f"✅ Meeting Scheduled: '{meeting.get('title', 'Meeting')}'\n"
                f"📅 {start_dt.strftime('%A, %B %d, %Y')} at {start_dt.strftime('%I:%M %p')}\n"
                f"⏱️ Duration: {meeting.get('duration', 60)} minutes\n"
                f"👥 Attendees: {', '.join(meeting.get('attendee_labels', []))}\n"
                f"🔗 Join Meeting:\n{teams_url}")
        send_whatsapp_message(WHATSAPP_TO_NUMBER, body)
        schedule_whatsapp_reminder(meeting, WHATSAPP_TO_NUMBER, teams_url)

    if do_call and CALL_TO_NUMBER:
        message = (f"Hello! Your Teams meeting has been scheduled. Title: {meeting.get('title', 'your meeting')}. "
                   f"Date: {start_dt.strftime('%A, %B %d, %Y')}. Time: {start_dt.strftime('%I:%M %p')}. "
                   f"Duration: {meeting.get('duration', 60)} minutes. Please check your WhatsApp for the meeting link. Thank you.")
        make_ivr_call(CALL_TO_NUMBER, message)
        schedule_call_reminder(meeting, CALL_TO_NUMBER)

    if do_sms and SMS_TO_NUMBER:
        body = (f"Meeting Scheduled: {meeting.get('title', 'Meeting')}\n"
                f"Date: {start_dt.strftime('%A, %B %d, %Y')}\nTime: {start_dt.strftime('%I:%M %p')}\n"
                f"Duration: {meeting.get('duration', 60)} min\nAttendees: {', '.join(meeting.get('attendee_labels', []))}\n")
        if teams_url: body += f"Join: {teams_url}"
        send_sms(SMS_TO_NUMBER, body)
        schedule_sms_reminder(meeting, SMS_TO_NUMBER, teams_url)
