"""
api/server.py — Zoom Agent
Converted from: marketplace copy/backend/tools/zoom.js

Auth: OAuth2 (ZOOM_CLIENT_ID / ZOOM_CLIENT_SECRET)
Scopes configured in Zoom App Marketplace portal (meeting:write, meeting:read, recording:read)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

load_dotenv()
logger = logging.getLogger(__name__)

app = FastAPI(title="Zoom Agent API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ZOOM_API = "https://api.zoom.us/v2"


class AgentTaskRequest(BaseModel):
    taskId: str
    userId: str
    agentId: str
    action: str
    access_token: str | None = None
    # create_meeting
    topic: str | None = None
    start_time: str | None = None   # UTC ISO format e.g. 2024-05-20T10:00:00Z
    duration: int | None = None     # in minutes
    # list_upcoming_meetings
    type: str | None = None         # 'scheduled' | 'upcoming'
    # get_meeting_summary
    meetingId: str | None = None
    model_config = ConfigDict(extra="allow")


class AgentTaskResponse(BaseModel):
    status: str
    type: str | None = None
    error: str | None = None
    message: str | None = None
    data: dict | None = None
    displayName: str | None = None


@app.post("/zoom/action", response_model=AgentTaskResponse)
def execute_zoom_action(req: AgentTaskRequest) -> AgentTaskResponse:
    """
    Mirrors the execute() switch in zoom.js.
    Calls the Zoom REST API v2 with OAuth2 Bearer token.
    """
    action = req.action
    token = req.access_token

    if not token:
        return AgentTaskResponse(
            status="failed",
            error="Zoom access token is missing. Please connect your Zoom account.",
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        # ── create_meeting ────────────────────────────────────────────────────
        if action == "create_meeting":
            if not req.topic:
                return AgentTaskResponse(status="failed", error="topic is required.")

            # Default start time: 30 minutes from now — mirrors JS: Date.now() + 30 * 60000
            default_start = (
                datetime.now(tz=timezone.utc) + timedelta(minutes=30)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")

            payload = {
                "topic": req.topic,
                "type": 2,  # 2 = scheduled meeting — mirrors JS comment
                "start_time": req.start_time or default_start,
                "duration": req.duration or 30,
                "settings": {
                    "host_video": True,
                    "participant_video": True,
                    "join_before_host": False,
                    "waiting_room": True,
                },
            }
            res = requests.post(
                f"{ZOOM_API}/users/me/meetings",
                headers=headers,
                json=payload,
                timeout=15,
            )
            res.raise_for_status()
            d = res.json()
            return AgentTaskResponse(
                status="success",
                type="zoom_action",
                message="Meeting successfully created.",
                data={
                    "topic": d.get("topic"),
                    "join_url": d.get("join_url"),
                    "start_time": d.get("start_time"),
                    "duration": d.get("duration"),
                    "password": d.get("password") or "No password",
                    "id": d.get("id"),
                },
                displayName=d.get("topic"),
            )

        # ── list_upcoming_meetings ─────────────────────────────────────────────
        elif action == "list_upcoming_meetings":
            meeting_type = req.type or "upcoming"
            res = requests.get(
                f"{ZOOM_API}/users/me/meetings",
                headers=headers,
                params={"type": meeting_type},
                timeout=10,
            )
            res.raise_for_status()
            d = res.json()
            meetings = [
                {
                    "id": m.get("id"),
                    "topic": m.get("topic"),
                    "startTime": m.get("start_time"),
                    "duration": m.get("duration"),
                    "joinUrl": m.get("join_url"),
                }
                for m in (d.get("meetings") or [])
            ]
            return AgentTaskResponse(
                status="success",
                type="zoom_list",
                message=f"Found {len(meetings)} upcoming meeting(s).",
                data={"meetings": meetings},
                displayName="Upcoming Meetings",
            )

        # ── get_meeting_summary ───────────────────────────────────────────────
        elif action == "get_meeting_summary":
            if not req.meetingId:
                return AgentTaskResponse(status="failed", error="meetingId is required.")

            try:
                res = requests.get(
                    f"{ZOOM_API}/meetings/{req.meetingId}/meeting_summary",
                    headers=headers,
                    timeout=10,
                )
                res.raise_for_status()
                d = res.json()
                return AgentTaskResponse(
                    status="success",
                    type="zoom_summary",
                    message=f"Summary retrieved for meeting {req.meetingId}.",
                    data={
                        "meetingId": d.get("meeting_id"),
                        "summary": d.get("summary_details"),  # array of summary sections
                    },
                    displayName="Meeting Summary",
                )
            except requests.exceptions.HTTPError as summary_err:
                # Mirrors the JS inner try/catch — gracefully handles plan restriction
                error_msg = summary_err.response.json().get("message", summary_err.response.text) if summary_err.response else str(summary_err)
                return AgentTaskResponse(
                    status="failed",
                    error="Failed to fetch summary. Note: Zoom restricts this endpoint to Paid accounts with AI companion enabled.",
                    data={"details": error_msg},
                )

        else:
            return AgentTaskResponse(status="failed", error=f"Unknown action: {action}")

    except requests.exceptions.HTTPError as e:
        logger.exception("Zoom API HTTP error")
        return AgentTaskResponse(status="failed", error=f"Zoom API error: {e.response.text}")
    except Exception as e:
        logger.exception("Zoom agent error")
        return AgentTaskResponse(status="failed", error=str(e))


@app.get("/health")
def health():
    return {"status": "healthy", "agent": "zoom-agent"}
