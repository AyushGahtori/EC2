"""
Calendar Agent
Handles Google Calendar operations: create, list, update, delete events
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

CALENDAR_BASE_URL = "https://www.googleapis.com/calendar/v3"


class CalendarAgent(BaseAgent):
    """Agent for Google Calendar operations."""

    async def handle(self, user_message: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Determine action and execute calendar operation."""
        action = await self._determine_action(user_message, context)
        logger.info(f"[calendar] action: {action}")

        if action == "create":
            return await self.create_event(user_message, context)
        if action == "list":
            return await self.list_events(user_message, context)
        if action == "update":
            return await self.update_event(user_message)
        if action == "delete":
            return await self.delete_event(user_message)
        return await self.list_events(user_message, context)

    async def _determine_action(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Use LLM to determine the calendar action."""
        params = await self.extract_parameters(
            user_message=user_message,
            schema_description='action: one of "create", "list", "update", "delete"',
            example_output='{"action": "create"}',
            context=context,
        )
        return params.get("action", "list")

    async def create_event(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a new calendar event."""
        now = datetime.utcnow()
        params = await self.extract_parameters(
            user_message=user_message,
            schema_description=f"""
- title: event title or name
- start_datetime: ISO 8601 datetime (today is {now.isoformat()})
- end_datetime: ISO 8601 datetime (default 1 hour after start)
- description: optional event description
- attendees: list of email addresses (empty if not mentioned)
- location: optional location
            """,
            example_output='''{
  "title": "Team Meeting",
  "start_datetime": "2026-03-27T14:00:00",
  "end_datetime": "2026-03-27T15:00:00",
  "description": null,
  "attendees": [],
  "location": null
}''',
            context=context,
        )

        title = (params.get("title") or "").strip()
        start_datetime = params.get("start_datetime")
        if not title or not start_datetime:
            missing = []
            if not title:
                missing.append("title")
            if not start_datetime:
                missing.append("date and time")
            return self.failure(
                f"I need the event {' and '.join(missing)} before I can create it."
            )

        end_datetime = params.get("end_datetime")
        if not end_datetime:
            try:
                parsed_start = datetime.fromisoformat(start_datetime.replace("Z", "+00:00"))
                end_datetime = (parsed_start + timedelta(hours=1)).isoformat()
            except ValueError:
                end_datetime = (now + timedelta(hours=1)).isoformat()

        event = {
            "summary": title,
            "start": {"dateTime": start_datetime, "timeZone": "UTC"},
            "end": {"dateTime": end_datetime, "timeZone": "UTC"},
        }
        if params.get("description"):
            event["description"] = params["description"]
        if params.get("location"):
            event["location"] = params["location"]
        if params.get("attendees"):
            event["attendees"] = [{"email": email} for email in params["attendees"]]

        try:
            response = await self.request_google_api(
                "POST",
                f"{CALENDAR_BASE_URL}/calendars/primary/events",
                json=event,
            )
        except Exception as exc:
            return self.handle_google_exception("Calendar", exc, data={"event": event})

        if response.status_code in (200, 201):
            data = response.json()
            return self.success(
                summary=f"Created event '{title}' on {start_datetime}",
                data={"event_id": data.get("id"), "html_link": data.get("htmlLink"), "event": data},
            )

        return self.handle_google_api_error(
            "Calendar",
            response,
            data={"event": event},
        )

    async def list_events(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """List upcoming calendar events."""
        params = await self.extract_parameters(
            user_message=user_message,
            schema_description="""
- range_days: number of days to look ahead
- max_results: maximum number of events to list
            """,
            example_output='{"range_days": 7, "max_results": 10}',
            context=context,
        )

        lower_message = user_message.lower()
        default_range_days = 30 if "all" in lower_message else 7
        default_max_results = 25 if "all" in lower_message else 10

        range_days = params.get("range_days") or default_range_days
        max_results = params.get("max_results") or default_max_results

        try:
            range_days = max(1, min(int(range_days), 365))
        except (TypeError, ValueError):
            range_days = default_range_days

        try:
            max_results = max(1, min(int(max_results), 50))
        except (TypeError, ValueError):
            max_results = default_max_results

        now = datetime.utcnow()
        time_min = now.isoformat() + "Z"
        time_max = (now + timedelta(days=range_days)).isoformat() + "Z"

        try:
            response = await self.request_google_api(
                "GET",
                f"{CALENDAR_BASE_URL}/calendars/primary/events",
                params={
                    "timeMin": time_min,
                    "timeMax": time_max,
                    "maxResults": max_results,
                    "singleEvents": True,
                    "orderBy": "startTime",
                },
                retry_on_failure=True,
            )
        except Exception as exc:
            return self.handle_google_exception("Calendar", exc)

        if response.status_code == 200:
            data = response.json()
            events = data.get("items", [])
            if not events:
                return self.success(summary="No upcoming events found.", data={"events": []})

            event_list = []
            for event in events:
                start_value = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date", "All day")
                summary = event.get("summary", "Untitled event")
                event_list.append(f"- {summary} - {start_value}")

            return self.success(
                summary=f"Found {len(events)} calendar events:\n" + "\n".join(event_list),
                data={"events": events},
            )

        return self.handle_google_api_error("Calendar", response)

    async def update_event(self, user_message: str) -> Dict[str, Any]:
        """Update an existing event (placeholder - requires event ID)."""
        return self.success(
            summary="To update an event, please specify the event title and what to change. I'll find it and update it.",
            data={"action": "update_requested"},
        )

    async def delete_event(self, user_message: str) -> Dict[str, Any]:
        """Delete a calendar event (placeholder - requires event ID)."""
        return self.success(
            summary="To delete an event, please specify the event title. I'll find it and remove it.",
            data={"action": "delete_requested"},
        )
