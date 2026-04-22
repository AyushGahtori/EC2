"""
LinkedIn agent API.

Mirrors the JS tool behavior while using the detached EC2 Firestore bootstrap.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ec2_shared.agent_runtime import auth_required_response, resolve_provider_credentials
from ec2_shared.firestore_store import get_db
from ec2_shared.api_security import apply_api_security
from firebase_admin import firestore

load_dotenv()
logger = logging.getLogger(__name__)
db = get_db()

app = FastAPI(title="LinkedIn Agent API", version="1.0.0")
apply_api_security(app)

LINKEDIN_API_V2 = "https://api.linkedin.com/v2"
LINKEDIN_API_REST = "https://api.linkedin.com/rest"
LINKEDIN_API_VERSION = "202405"


class AgentTaskRequest(BaseModel):
    taskId: str | None = None
    userId: str | None = None
    agentId: str | None = None
    action: str
    access_token: str | None = None
    urn: str | None = None
    content: str | None = None
    scheduled_time: str | None = None
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


@app.post("/linkedin/action", response_model=AgentTaskResponse)
def execute_linkedin_action(req: AgentTaskRequest) -> AgentTaskResponse:
    credentials = resolve_provider_credentials(
        user_id=req.userId,
        provider="linkedin",
        access_token=req.access_token,
    )
    token = credentials.get("access_token")
    urn = req.urn or credentials.get("urn")

    if not token:
        return AgentTaskResponse(
            **auth_required_response(
                agent_slug="linkedin",
                agent_id="linkedin-agent",
                provider="linkedin",
                message="LinkedIn access token is missing. Please connect your LinkedIn account.",
            )
        )

    action = req.action

    try:
        if action == "schedule_post":
            if not req.content:
                return AgentTaskResponse(status="failed", error="content is required.")
            if not urn:
                return AgentTaskResponse(
                    status="failed",
                    error="Missing LinkedIn URN. Please re-connect the integration.",
                )

            if req.scheduled_time and req.userId:
                try:
                    scheduled_dt = datetime.fromisoformat(req.scheduled_time.replace("Z", "+00:00"))
                    if scheduled_dt > datetime.now(scheduled_dt.tzinfo) and (
                        scheduled_dt - datetime.now(scheduled_dt.tzinfo)
                    ).total_seconds() > 60:
                        db.collection("scheduled_tasks").add(
                            {
                                "uid": req.userId,
                                "qualifiedName": "linkedin__schedule_post",
                                "args": {"content": req.content},
                                "scheduledFor": scheduled_dt.isoformat(),
                                "status": "pending",
                                "createdAt": firestore.SERVER_TIMESTAMP,
                            }
                        )
                        return AgentTaskResponse(
                            status="success",
                            type="linkedin_action",
                            message=(
                                "Post successfully queued to be published safely at "
                                f"{scheduled_dt.isoformat()}"
                            ),
                            displayName="LinkedIn Post Scheduled",
                        )
                except (ValueError, TypeError):
                    pass

            headers = {
                "Authorization": f"Bearer {token}",
                "X-Restli-Protocol-Version": "2.0.0",
                "LinkedIn-Version": LINKEDIN_API_VERSION,
                "Content-Type": "application/json",
            }
            body = {
                "author": urn,
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {"text": req.content},
                        "shareMediaCategory": "NONE",
                    }
                },
                "visibility": {
                    "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC",
                },
            }

            # Try legacy UGC endpoint first for parity with existing flow.
            response = requests.post(
                f"{LINKEDIN_API_V2}/ugcPosts",
                headers=headers,
                json=body,
                timeout=15,
            )

            # If LinkedIn rejects UGC versioning, retry through REST posts API.
            if response.status_code in {400, 403, 404} and "NO_VERSION" in response.text:
                response = requests.post(
                    f"{LINKEDIN_API_REST}/posts",
                    headers=headers,
                    json={
                        "author": urn,
                        "commentary": req.content,
                        "visibility": "PUBLIC",
                        "distribution": {
                            "feedDistribution": "MAIN_FEED",
                            "targetEntities": [],
                            "thirdPartyDistributionChannels": [],
                        },
                        "lifecycleState": "PUBLISHED",
                        "isReshareDisabledByAuthor": False,
                    },
                    timeout=15,
                )

            if response.status_code == 422:
                api_error = response.json()
                if "duplicate" in str(api_error.get("message", "")).lower():
                    return AgentTaskResponse(
                        status="failed",
                        error=(
                            "LinkedIn blocked this post because it is a duplicate of a recent post: "
                            f"{api_error.get('message', '')}"
                        ),
                    )

            response.raise_for_status()
            payload = response.json()
            return AgentTaskResponse(
                status="success",
                type="linkedin_action",
                message="Successfully posted on LinkedIn!",
                displayName="LinkedIn Post",
                data={"success": True, "postId": payload.get("id"), "message": "Successfully posted on LinkedIn!"},
            )

        if action == "analyze_engagement":
            return AgentTaskResponse(
                status="success",
                type="linkedin_info",
                message=(
                    "Detailed engagement analytics demand 'Community Management API' approval from LinkedIn. "
                    "I can currently help you post and schedule content natively."
                ),
                displayName="Engagement Analytics",
            )

        return AgentTaskResponse(status="failed", error=f"Unknown action: {action}")
    except requests.HTTPError as exc:
        api_error: dict[str, object] = {}
        try:
            api_error = exc.response.json()
        except Exception:
            pass
        logger.exception("LinkedIn API HTTP error")
        return AgentTaskResponse(
            status="failed",
            error=(
                "Failed to post on LinkedIn. Error: "
                f"{api_error.get('message') or exc.response.text} "
                f"(HTTP {exc.response.status_code}). "
                "Check permissions if this is the first attempt."
            ),
        )
    except Exception as exc:
        logger.exception("LinkedIn agent error")
        return AgentTaskResponse(status="failed", error=str(exc))


@app.get("/health")
def health():
    return {"status": "healthy", "agent": "linkedin-agent"}
