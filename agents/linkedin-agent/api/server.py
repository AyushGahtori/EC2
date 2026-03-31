"""
api/server.py — LinkedIn Agent
Converted from: marketplace copy/backend/tools/linkedin.js

Auth: OAuth2 (LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET)
Scopes: w_member_social, openid, profile, email
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

import requests
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

load_dotenv()
logger = logging.getLogger(__name__)

# ── Firebase init (for scheduled post queue — mirrors LinkedIn JS) ─────────────
_KEY_PATH = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY", "/home/ubuntu/app/.secrets/serviceAccountKey.json")
if not firebase_admin._apps:
    cred = credentials.Certificate(_KEY_PATH)
    firebase_admin.initialize_app(cred)

db = firestore.client()

app = FastAPI(title="LinkedIn Agent API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

LINKEDIN_API = "https://api.linkedin.com/v2"


class AgentTaskRequest(BaseModel):
    taskId: str
    userId: str
    agentId: str
    action: str
    access_token: str | None = None
    # LinkedIn URN — the user's LinkedIn person ID (urn:li:person:XXXXX)
    urn: str | None = None
    # schedule_post
    content: str | None = None
    scheduled_time: str | None = None  # ISO datetime string
    model_config = ConfigDict(extra="allow")


class AgentTaskResponse(BaseModel):
    status: str
    type: str | None = None
    error: str | None = None
    message: str | None = None
    data: dict | None = None
    displayName: str | None = None


@app.post("/linkedin/action", response_model=AgentTaskResponse)
def execute_linkedin_action(req: AgentTaskRequest) -> AgentTaskResponse:
    """
    Mirrors the execute() switch in linkedin.js.
    Includes background scheduling via Firestore (same as JS version with firebase-admin).
    """
    action = req.action
    token = req.access_token
    user_id = req.userId

    if not token:
        return AgentTaskResponse(
            status="failed",
            error="LinkedIn access token is missing. Please connect your LinkedIn account.",
        )

    try:
        # ── schedule_post ─────────────────────────────────────────────────────
        if action == "schedule_post":
            if not req.content:
                return AgentTaskResponse(status="failed", error="content is required.")

            if not req.urn:
                return AgentTaskResponse(
                    status="failed",
                    error="Missing LinkedIn URN. Please re-connect the integration.",
                )

            # Handle background scheduling — mirrors JS scheduled_time logic exactly
            if req.scheduled_time and user_id:
                try:
                    scheduled_dt = datetime.fromisoformat(
                        req.scheduled_time.replace("Z", "+00:00")
                    )
                    now = datetime.utcnow()
                    # Only schedule if it's more than 60 seconds in the future
                    if (scheduled_dt.replace(tzinfo=None) - now).total_seconds() > 60:
                        # Scoped under users/{userId}/scheduled_tasks — prevents cross-user access
                        db.collection("users").document(user_id).collection("scheduled_tasks").add(
                            {
                                "uid": user_id,
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
                            message=f"Post successfully queued to be published at {scheduled_dt.strftime('%b %d, %Y %H:%M')}.",
                            displayName="LinkedIn Post Scheduled",
                        )
                except (ValueError, TypeError):
                    pass  # Invalid date — fall through to immediate post

            # ── Immediate post to LinkedIn ─────────────────────────────────
            # POST /ugcPosts — mirrors JS payload exactly
            post_headers = {
                "Authorization": f"Bearer {token}",
                "X-Restli-Protocol-Version": "2.0.0",
                "Content-Type": "application/json",
            }
            payload = {
                "author": req.urn,
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {"text": req.content},
                        "shareMediaCategory": "NONE",
                    }
                },
                "visibility": {
                    "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
                },
            }
            res = requests.post(
                f"{LINKEDIN_API}/ugcPosts",
                headers=post_headers,
                json=payload,
                timeout=15,
            )

            # Handle duplicate post error exactly as the JS does
            if res.status_code == 422:
                error_data = res.json()
                error_msg = error_data.get("message", "")
                if "duplicate" in error_msg.lower():
                    return AgentTaskResponse(
                        status="failed",
                        error=f"LinkedIn blocked this post because it is a duplicate of a recent post: {error_msg}",
                    )

            res.raise_for_status()
            data = res.json()
            return AgentTaskResponse(
                status="success",
                type="linkedin_action",
                message="Successfully posted on LinkedIn!",
                data={"postId": data.get("id")},
                displayName="LinkedIn Post",
            )

        # ── analyze_engagement ────────────────────────────────────────────────
        elif action == "analyze_engagement":
            # Mirrors JS response — LinkedIn restricts analytics to approved Community Management API
            return AgentTaskResponse(
                status="success",
                type="linkedin_info",
                message=(
                    "Detailed engagement analytics requires 'Community Management API' approval from LinkedIn. "
                    "I can currently help you post and schedule content natively."
                ),
                displayName="Engagement Analytics",
            )

        else:
            return AgentTaskResponse(status="failed", error=f"Unknown action: {action}")

    except requests.exceptions.HTTPError as e:
        error_data = {}
        try:
            error_data = e.response.json()
        except Exception:
            pass
        error_msg = error_data.get("message") or e.response.text
        logger.exception("LinkedIn API HTTP error")
        return AgentTaskResponse(
            status="failed",
            error=f"Failed to post on LinkedIn. Error: {error_msg}. Check permissions if this is the first attempt.",
        )
    except Exception as e:
        logger.exception("LinkedIn agent error")
        return AgentTaskResponse(status="failed", error=str(e))


@app.get("/health")
def health():
    return {"status": "healthy", "agent": "linkedin-agent"}
