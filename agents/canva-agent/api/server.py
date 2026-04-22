"""
Canva agent API.

The JS source of truth currently exposes Canva as a coming-soon integration.
We keep the action list and auth shape, but return the same placeholder result.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ec2_shared.agent_runtime import auth_required_response, resolve_provider_credentials
from ec2_shared.api_security import apply_api_security

load_dotenv()
logger = logging.getLogger(__name__)

app = FastAPI(title="Canva Agent API", version="1.0.0")
apply_api_security(app)


class AgentTaskRequest(BaseModel):
    taskId: str | None = None
    userId: str | None = None
    agentId: str | None = None
    action: str
    access_token: str | None = None
    limit: int | None = None
    title: str | None = None
    type: str | None = None
    model_config = ConfigDict(extra="allow")


class AgentTaskResponse(BaseModel):
    status: str
    type: str | None = None
    error: str | None = None
    message: str | None = None
    data: dict | None = None
    displayName: str | None = None
    auth_url: str | None = None
    provider: str | None = None
    agentId: str | None = None
    bundleId: str | None = None


@app.post("/canva/action", response_model=AgentTaskResponse)
def execute_canva_action(req: AgentTaskRequest) -> AgentTaskResponse:
    credentials = resolve_provider_credentials(
        user_id=req.userId,
        provider="canva",
        access_token=req.access_token,
    )
    if not credentials.get("access_token"):
        return AgentTaskResponse(
            **auth_required_response(
                agent_slug="canva",
                agent_id="canva-agent",
                provider="canva",
                message="Canva access token is missing. Please connect your Canva account.",
            )
        )

    return AgentTaskResponse(
        status="failed",
        error="Canva integration is coming soon.",
        message="Canva integration is coming soon.",
    )


@app.get("/health")
def health():
    return {"status": "healthy", "agent": "canva-agent"}
