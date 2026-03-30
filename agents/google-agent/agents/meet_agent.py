"""
Google Meet Agent
Schedule meetings and generate Meet links
"""

import logging
import os
import re
from datetime import datetime, timedelta
from typing import Dict, Any

from agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

MEET_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
EMAIL_REGEX = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
TIME_REGEX = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.IGNORECASE)


class MeetAgent(BaseAgent):
    """Agent for Google Meet operations via Calendar API."""

    async def handle(self, user_message: str, context: Dict[str, Any]) -> Dict[str, Any]:
        params = await self.extract_parameters(
            user_message=user_message,
            schema_description="""
- title: meeting title
- start_datetime: ISO 8601 datetime
- attendees: list of email addresses
- duration_minutes: meeting duration (default 60)
            """,
            example_output='''{
  "title": "Team Standup",
  "start_datetime": "2026-03-27T10:00:00+05:30",
  "attendees": ["alice@example.com", "bob@example.com"],
  "duration_minutes": 30
}''',
            context=context,
        )

        attendees = params.get("attendees") or self._extract_email_addresses(user_message)
        title = (params.get("title") or "").strip() or self._derive_title(attendees)
        start_datetime = params.get("start_datetime") or self._infer_start_datetime(user_message)
        if not start_datetime:
            return self.failure("I need the meeting date and time before I can schedule a Google Meet.")

        try:
            parsed_start = datetime.fromisoformat(start_datetime.replace("Z", "+00:00"))
        except ValueError:
            return self.failure("I could not understand the meeting date and time. Please provide a clearer date/time.")

        duration_minutes = params.get("duration_minutes") or self._infer_duration_minutes(user_message)
        try:
            duration_minutes = max(15, min(int(duration_minutes), 480))
        except (TypeError, ValueError):
            duration_minutes = 60

        end_datetime = (parsed_start + timedelta(minutes=duration_minutes)).isoformat()
        timezone_name = os.getenv("DEFAULT_TIMEZONE", "Asia/Kolkata")

        event = {
            "summary": title,
            "start": {"dateTime": parsed_start.isoformat(), "timeZone": timezone_name},
            "end": {"dateTime": end_datetime, "timeZone": timezone_name},
            "conferenceData": {
                "createRequest": {
                    "requestId": f"meet-{int(datetime.utcnow().timestamp())}",
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
            "attendees": [{"email": email} for email in attendees],
        }

        try:
            response = await self.request_google_api(
                "POST",
                MEET_EVENTS_URL,
                params={"conferenceDataVersion": 1},
                json=event,
            )
        except Exception as exc:
            return self.handle_google_exception("Google Meet", exc, data={"event": event})

        if response.status_code in (200, 201):
            data = response.json()
            entry_points = data.get("conferenceData", {}).get("entryPoints", [])
            meet_link = next((entry.get("uri", "") for entry in entry_points if entry.get("uri")), "")
            return self.success(
                summary=f"Meeting scheduled: '{title}'\nMeet link: {meet_link or 'generated but not returned'}",
                data={"meet_link": meet_link, "event_id": data.get("id"), "event": data},
            )

        return self.handle_google_api_error("Google Meet", response, data={"event": event})

    def _extract_email_addresses(self, text: str):
        emails = EMAIL_REGEX.findall(text or "")
        unique_emails = []
        seen = set()
        for email in emails:
            normalized = email.strip().strip(".,;")
            if normalized and normalized not in seen:
                seen.add(normalized)
                unique_emails.append(normalized)
        return unique_emails

    def _derive_title(self, attendees):
        if attendees:
            first_attendee = attendees[0].split("@")[0].replace(".", " ").replace("_", " ").strip()
            if first_attendee:
                return f"Meeting with {first_attendee.title()}"
        return "Meeting"

    def _infer_start_datetime(self, text: str) -> str:
        lower_text = text.lower()
        now = datetime.now().astimezone()
        target_date = now.date()
        if "tomorrow" in lower_text:
            target_date = (now + timedelta(days=1)).date()
        elif "day after tomorrow" in lower_text:
            target_date = (now + timedelta(days=2)).date()
        elif "today" not in lower_text and "tomorrow" not in lower_text and "tonight" not in lower_text:
            return ""

        match = TIME_REGEX.search(lower_text)
        if not match:
            return ""

        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = match.group(3).lower()
        if meridiem == "pm" and hour != 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0

        inferred = now.replace(
            year=target_date.year,
            month=target_date.month,
            day=target_date.day,
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )
        return inferred.isoformat()

    def _infer_duration_minutes(self, text: str) -> int:
        match = re.search(r"\b(\d{1,3})\s*(minute|minutes|min|hour|hours|hr|hrs)\b", text, re.IGNORECASE)
        if not match:
            return 60

        value = int(match.group(1))
        unit = match.group(2).lower()
        if unit.startswith("hour") or unit.startswith("hr"):
            return value * 60
        return value
