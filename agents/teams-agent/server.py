"""
FastAPI server for the Teams agent.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - local fallback
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if load_dotenv:
    load_dotenv(BASE_DIR / ".env")

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from calendar_agent import run_calendar_action
from ec2_shared.agent_runtime import auth_required_response, resolve_provider_credentials
from ec2_shared.oauth_router import OAuthAgentRegistration, register_oauth_routes
from email_agent import run_email_action
from graph_client import GRAPH_SCOPES
from teams_agent import run_teams_action

logger = logging.getLogger("teams-agent")
logger.setLevel(logging.INFO)


class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "service": "teams-agent",
            "message": record.getMessage(),
            "route": getattr(record, "route", None),
            "taskId": getattr(record, "taskId", None),
            "userId": getattr(record, "userId", None),
            "agentId": getattr(record, "agentId", None),
            "status": getattr(record, "status", None),
            "latency_ms": getattr(record, "latency_ms", None),
            "error": getattr(record, "error", None),
        }
        return json.dumps(log_entry)


if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
logger.propagate = False

app = FastAPI(
    title="SnitchX Teams Agent",
    description="Microsoft Teams agent for SnitchX - handles calls, email, and calendar actions.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TeamsActionRequest(BaseModel):
    action: str
    contact: str | None = None
    message: str | None = None
    title: str | None = None
    attendees: list[str] | None = None
    date: str | None = None
    time: str | None = None
    duration: int | None = None
    description: str | None = None
    taskId: str | None = None
    userId: str | None = None
    agentId: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None


class TeamsActionResponse(BaseModel):
    status: str
    type: str | None = None
    message: str | None = None
    url: str | None = None
    displayName: str | None = None
    email: str | None = None
    teamsUrl: str | None = None
    outlookUrl: str | None = None
    title: str | None = None
    date: str | None = None
    time: str | None = None
    duration: int | None = None
    resolvedAttendees: list[dict] | None = None
    unresolvedAttendees: list[str] | None = None
    description: str | None = None
    error: str | None = None
    flow: dict | None = None
    auth_url: str | None = None
    provider: str | None = None
    agentId: str | None = None
    bundleId: str | None = None


def _microsoft_auth_required(message: str) -> dict:
    return auth_required_response(
        agent_slug="teams",
        agent_id="teams-agent",
        provider="microsoft",
        bundle_id="microsoft-bundle",
        message=message,
    )


def _inject_microsoft_credentials(payload: dict) -> tuple[dict | None, dict | None]:
    credentials = resolve_provider_credentials(
        user_id=payload.get("userId"),
        provider="microsoft",
        access_token=payload.get("access_token"),
        refresh_token=payload.get("refresh_token"),
    )
    if not credentials.get("access_token") and not credentials.get("refresh_token"):
        return None, _microsoft_auth_required(
            "Microsoft 365 access token is missing. Please connect your Microsoft account."
        )

    merged_payload = dict(payload)
    for key, value in credentials.items():
        if value is not None:
            merged_payload[key] = value
    return merged_payload, None


def _normalize_microsoft_result(result: dict) -> dict:
    if result.get("status") == "action_required":
        return _microsoft_auth_required(
            "Microsoft 365 connection is required before this action can run."
        )
    return result


def _log_extra(route: str, payload: dict) -> dict:
    return {
        "route": route,
        "taskId": payload.get("taskId"),
        "userId": payload.get("userId"),
        "agentId": payload.get("agentId"),
    }


register_oauth_routes(
    app,
    OAuthAgentRegistration(
        provider="microsoft",
        agent_slug="teams",
        display_name="Microsoft 365",
        default_bundle_id="microsoft-bundle",
        default_scopes=list(GRAPH_SCOPES),
    ),
)


@app.get("/health")
async def health_check():
    return {"status": "healthy", "agent": "teams-agent", "version": "1.0.0"}


@app.post("/teams/action", response_model=TeamsActionResponse)
async def teams_action(data: TeamsActionRequest, request: Request):
    start_time = time.time()
    payload = data.model_dump()
    extra = _log_extra("/teams/action", payload)
    logger.info("Teams action request received", extra=extra)

    hydrated_payload, auth_failure = _inject_microsoft_credentials(payload)
    if auth_failure:
        logger.info("Teams action blocked pending auth", extra=extra)
        return TeamsActionResponse(**auth_failure)

    try:
        result = _normalize_microsoft_result(run_teams_action(hydrated_payload))
        latency = int((time.time() - start_time) * 1000)
        extra.update({"status": result.get("status"), "latency_ms": latency})
        if result.get("status") == "failed":
            extra["error"] = result.get("error")
        logger.info("Teams action completed", extra=extra)
        return TeamsActionResponse(**result)
    except Exception as exc:
        latency = int((time.time() - start_time) * 1000)
        extra.update({"status": "failed", "latency_ms": latency, "error": str(exc)})
        logger.error("Teams action failed", extra=extra)
        raise HTTPException(status_code=500, detail=f"Agent execution failed: {exc}") from exc


@app.post("/email/action")
async def email_action(data: dict, request: Request):
    start_time = time.time()
    extra = _log_extra("/email/action", data)
    logger.info("Email action request received", extra=extra)

    hydrated_payload, auth_failure = _inject_microsoft_credentials(data)
    if auth_failure:
        logger.info("Email action blocked pending auth", extra=extra)
        return auth_failure

    try:
        result = _normalize_microsoft_result(run_email_action(hydrated_payload))
        latency = int((time.time() - start_time) * 1000)
        extra.update({"status": result.get("status"), "latency_ms": latency})
        if result.get("status") == "failed":
            extra["error"] = result.get("error")
        logger.info("Email action completed", extra=extra)
        return result
    except Exception as exc:
        latency = int((time.time() - start_time) * 1000)
        extra.update({"status": "failed", "latency_ms": latency, "error": str(exc)})
        logger.error("Email action failed", extra=extra)
        raise HTTPException(status_code=500, detail=f"Agent execution failed: {exc}") from exc


@app.post("/calendar/action")
async def calendar_action(data: dict, request: Request):
    start_time = time.time()
    extra = _log_extra("/calendar/action", data)
    logger.info("Calendar action request received", extra=extra)

    hydrated_payload, auth_failure = _inject_microsoft_credentials(data)
    if auth_failure:
        logger.info("Calendar action blocked pending auth", extra=extra)
        return auth_failure

    try:
        result = _normalize_microsoft_result(run_calendar_action(hydrated_payload))
        latency = int((time.time() - start_time) * 1000)
        extra.update({"status": result.get("status"), "latency_ms": latency})
        if result.get("status") == "failed":
            extra["error"] = result.get("error")
        logger.info("Calendar action completed", extra=extra)
        return result
    except Exception as exc:
        latency = int((time.time() - start_time) * 1000)
        extra.update({"status": "failed", "latency_ms": latency, "error": str(exc)})
        logger.error("Calendar action failed", extra=extra)
        raise HTTPException(status_code=500, detail=f"Agent execution failed: {exc}") from exc


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8100"))
    uvicorn.run(app, host="0.0.0.0", port=port)
