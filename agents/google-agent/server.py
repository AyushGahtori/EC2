"""
FastAPI server for the Google Workspace agent.
"""

import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

load_dotenv(BASE_DIR / ".env")

from agents.calendar_agent import CalendarAgent
from agents.calling_agent import CallingAgent
from agents.contacts_agent import ContactsAgent
from agents.drive_agent import DriveAgent
from agents.gmail_agent import GmailAgent
from agents.meet_agent import MeetAgent
from agents.tasks_agent import TasksAgent
from agents.web_search_agent import WebSearchAgent
from ec2_shared.agent_runtime import auth_required_response, resolve_provider_credentials
from ec2_shared.oauth_router import OAuthAgentRegistration, register_oauth_routes
from google_client import GOOGLE_SCOPES, GoogleAuthRequired

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Google Workspace Agent...")
    yield
    logger.info("Shutting down Google Workspace Agent...")


app = FastAPI(
    title="SnitchX Google Workspace Agent",
    description="Agent for Google Services (Gmail, Calendar, Meet, Tasks, Drive)",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class GoogleActionRequest(BaseModel):
    agent_type: str
    action: str
    parameters: str | None = None
    taskId: str | None = None
    userId: str | None = None
    agentId: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None


class GoogleActionResponse(BaseModel):
    status: str
    type: str | None = None
    message: str | None = None
    agent_type: str | None = None
    action: str | None = None
    result: dict | str | list | None = None
    summary: str | None = None
    error: str | None = None
    execution_time_ms: float | None = None
    auth_url: str | None = None
    provider: str | None = None
    agentId: str | None = None
    bundleId: str | None = None


AGENT_MAP = {
    "calendar": CalendarAgent,
    "gmail": GmailAgent,
    "meet": MeetAgent,
    "contacts": ContactsAgent,
    "drive": DriveAgent,
    "calling": CallingAgent,
    "web_search": WebSearchAgent,
    "tasks": TasksAgent,
}


def _google_auth_required(message: str) -> dict:
    return auth_required_response(
        agent_slug="google",
        agent_id="google-agent",
        provider="google",
        bundle_id="google-bundle",
        message=message,
    )


register_oauth_routes(
    app,
    OAuthAgentRegistration(
        provider="google",
        agent_slug="google",
        display_name="Google Workspace",
        default_bundle_id="google-bundle",
        default_scopes=list(GOOGLE_SCOPES),
    ),
)


@app.get("/health")
async def health_check():
    return {"status": "healthy", "agent": "google-agent", "version": "1.0.0"}


@app.post("/google/action", response_model=GoogleActionResponse)
async def google_action(data: GoogleActionRequest):
    agent_class = AGENT_MAP.get(data.agent_type.lower() if data.agent_type else "")
    if not agent_class:
        raise HTTPException(status_code=400, detail=f"Unknown or missing agent_type: {data.agent_type}")

    start = time.time()
    credentials = resolve_provider_credentials(
        user_id=data.userId,
        provider="google",
        access_token=data.access_token,
        refresh_token=data.refresh_token,
    )
    access_token = credentials.get("access_token")
    refresh_token = credentials.get("refresh_token")

    if not access_token and not refresh_token:
        return GoogleActionResponse(
            execution_time_ms=(time.time() - start) * 1000,
            **_google_auth_required(
                "Google access token is missing. Please connect your Google account."
            ),
        )

    try:
        agent = agent_class(
            access_token=access_token or "",
            user_id=data.userId or "default_user",
            refresh_token=refresh_token or "",
        )

        user_message = f"{data.action} {data.parameters or ''}"
        result = await agent.handle(
            user_message=user_message,
            context={"direct": True, "taskId": data.taskId},
        )

        if result.get("status") == "action_required":
            return GoogleActionResponse(
                execution_time_ms=(time.time() - start) * 1000,
                **_google_auth_required(
                    "Google account authorization is required before this action can run."
                ),
            )

        return GoogleActionResponse(
            status=result.get("status", "success"),
            type=f"google_{data.agent_type}",
            agent_type=data.agent_type,
            action=data.action,
            result=result.get("data", result),
            summary=result.get("summary"),
            execution_time_ms=(time.time() - start) * 1000,
            error=result.get("error"),
        )
    except GoogleAuthRequired:
        return GoogleActionResponse(
            execution_time_ms=(time.time() - start) * 1000,
            **_google_auth_required(
                "Google account authorization is required before this action can run."
            ),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Google Agent execution failed: {exc}",
        ) from exc


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8300"))
    uvicorn.run(app, host="0.0.0.0", port=port)
