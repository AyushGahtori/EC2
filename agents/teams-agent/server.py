"""
FastAPI server for the Teams Agent.

Exposes a POST /teams/action endpoint that receives task data
from the Firebase Cloud Function and returns structured results.

Run with:
    uvicorn server:app --host 0.0.0.0 --port 8100

Or via Docker (see Dockerfile).
"""

import os
import logging
import json
from datetime import datetime
import time

# Load .env file BEFORE importing teams_agent, because teams_agent.py
# reads env vars (GRAPH_CLIENT_ID, etc.) at module-level import time.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from teams_agent import run_teams_action
from email_agent import run_email_action
from calendar_agent import run_calendar_action
from graph_client import auth_store

# Setup structured JSON logging
logger = logging.getLogger("teams-agent")
logger.setLevel(logging.INFO)

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "service": "teams-agent",
            "message": record.getMessage(),
            "route": getattr(record, 'route', None),
            "taskId": getattr(record, 'taskId', None),
            "userId": getattr(record, 'userId', None),
            "agentId": getattr(record, 'agentId', None),
            "status": getattr(record, 'status', None),
            "latency_ms": getattr(record, 'latency_ms', None),
            "error": getattr(record, 'error', None),
        }
        return json.dumps(log_entry)

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
logger.propagate = False

app = FastAPI(
    title="SnitchX Teams Agent",
    description="Microsoft Teams agent for SnitchX — handles calls and messages.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class TeamsActionRequest(BaseModel):
    """Request body for the /teams/action endpoint."""
    action: str  # "make_call", "send_message", or "schedule_meeting"
    contact: str | None = None   # For make_call / send_message
    message: str | None = None   # Message text (for send_message) or description (for meeting)

    # Meeting-specific fields
    title: str | None = None
    attendees: list[str] | None = None
    date: str | None = None      # YYYY-MM-DD
    time: str | None = None      # HH:MM
    duration: int | None = None  # minutes
    description: str | None = None

    # Additional fields that may come from the Cloud Function
    taskId: str | None = None
    userId: str | None = None
    agentId: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None


class TeamsActionResponse(BaseModel):
    """Response body from the /teams/action endpoint."""
    status: str                             # "success" or "failed"
    type: str | None = None                 # "teams_call" | "teams_message" | "teams_meeting"
    # Call / Message fields
    url: str | None = None                  # msteams:// URL for call/message
    displayName: str | None = None
    email: str | None = None
    # Meeting fields
    teamsUrl: str | None = None             # https://teams.microsoft.com/l/meeting/...
    outlookUrl: str | None = None           # https://outlook.office.com/calendar/...
    title: str | None = None
    date: str | None = None
    time: str | None = None
    duration: int | None = None
    resolvedAttendees: list[dict] | None = None
    unresolvedAttendees: list[str] | None = None
    description: str | None = None
    error: str | None = None
    flow: dict | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring."""
    return {"status": "healthy", "agent": "teams-agent", "version": "1.0.0"}


@app.post("/teams/action", response_model=TeamsActionResponse)
async def teams_action(data: TeamsActionRequest, request: Request):
    """
    Execute a Teams action (make_call or send_message).

    Called by the Firebase Cloud Function when an agentTask is created
    with agentId="teams-agent".
    """
    start_time = time.time()
    extra = {
        'route': '/teams/action',
        'taskId': data.taskId,
        'userId': data.userId,
        'agentId': data.agentId,
    }
    logger.info("Teams action request received", extra=extra)
    try:
        result = run_teams_action(data.model_dump())
        latency = int((time.time() - start_time) * 1000)
        extra.update({'status': result.get('status'), 'latency_ms': latency})
        if result.get('status') == 'failed':
            extra['error'] = result.get('error')
        logger.info("Teams action completed", extra=extra)
        return TeamsActionResponse(**result)
    except Exception as exc:
        latency = int((time.time() - start_time) * 1000)
        extra.update({'status': 'failed', 'latency_ms': latency, 'error': str(exc)})
        logger.error("Teams action failed", extra=extra)
        raise HTTPException(
            status_code=500,
            detail=f"Agent execution failed: {str(exc)}",
        )

@app.post("/email/action")
async def email_action(data: dict, request: Request):
    """Execute an Email action."""
    start_time = time.time()
    extra = {
        'route': '/email/action',
        'taskId': data.get('taskId'),
        'userId': data.get('userId'),
        'agentId': data.get('agentId'),
    }
    logger.info("Email action request received", extra=extra)
    try:
        result = run_email_action(data)
        latency = int((time.time() - start_time) * 1000)
        extra.update({'status': result.get('status'), 'latency_ms': latency})
        if result.get('status') == 'failed':
            extra['error'] = result.get('error')
        logger.info("Email action completed", extra=extra)
        return result
    except Exception as exc:
        latency = int((time.time() - start_time) * 1000)
        extra.update({'status': 'failed', 'latency_ms': latency, 'error': str(exc)})
        logger.error("Email action failed", extra=extra)
        raise HTTPException(
            status_code=500,
            detail=f"Agent execution failed: {str(exc)}",
        )

@app.post("/calendar/action")
async def calendar_action(data: dict, request: Request):
    """Execute a Calendar action."""
    start_time = time.time()
    extra = {
        'route': '/calendar/action',
        'taskId': data.get('taskId'),
        'userId': data.get('userId'),
        'agentId': data.get('agentId'),
    }
    logger.info("Calendar action request received", extra=extra)
    try:
        result = run_calendar_action(data)
        latency = int((time.time() - start_time) * 1000)
        extra.update({'status': result.get('status'), 'latency_ms': latency})
        if result.get('status') == 'failed':
            extra['error'] = result.get('error')
        logger.info("Calendar action completed", extra=extra)
        return result
    except Exception as exc:
        latency = int((time.time() - start_time) * 1000)
        extra.update({'status': 'failed', 'latency_ms': latency, 'error': str(exc)})
        logger.error("Calendar action failed", extra=extra)
        raise HTTPException(
            status_code=500,
            detail=f"Agent execution failed: {str(exc)}",
        )


@app.post("/auth/poll")
def auth_poll():
    msal_app = auth_store.get("msal_app")
    flow = auth_store.get("flow")

    if not msal_app or not flow:
        raise HTTPException(status_code=400, detail="No active device flow.")

    result = msal_app.acquire_token_by_device_flow(flow, exit_condition=lambda f: True)

    if "access_token" in result:
        auth_store["token"] = result["access_token"]
        auth_store["flow"] = None
        return {"status": "authenticated"}

    error = result.get("error", "")
    if error == "authorization_pending":
        return {"status": "pending"}
    if error == "expired_token":
        auth_store["flow"] = None
        return {"status": "expired"}

    return {"status": "pending", "error": result.get("error_description", "")}

@app.get("/auth/status")
def auth_status():
    token = auth_store.get("token")
    if token:
        return {"authenticated": True}
    return {"authenticated": False}

@app.post("/auth/logout")
def auth_logout():
    auth_store["token"] = None
    auth_store["flow"] = None
    return {"status": "logged_out"}



# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8100"))
    uvicorn.run(app, host="0.0.0.0", port=port)
