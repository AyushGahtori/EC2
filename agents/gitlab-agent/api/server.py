"""
api/server.py — GitLab Agent
Converted from: marketplace copy/backend/tools/gitlab.js

Auth: OAuth2 (GITLAB_CLIENT_ID / GITLAB_CLIENT_SECRET)
Scopes: api, read_user, read_api
"""
from __future__ import annotations

import logging
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

load_dotenv()
logger = logging.getLogger(__name__)

app = FastAPI(title="GitLab Agent API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GITLAB_API = "https://gitlab.com/api/v4"


class AgentTaskRequest(BaseModel):
    taskId: str
    userId: str
    agentId: str
    action: str
    access_token: str | None = None
    # list_projects
    limit: int | None = None
    search: str | None = None
    # get_issue / create_issue
    projectId: str | None = None
    issueIid: int | None = None
    title: str | None = None
    description: str | None = None
    model_config = ConfigDict(extra="allow")


class AgentTaskResponse(BaseModel):
    status: str
    type: str | None = None
    error: str | None = None
    message: str | None = None
    data: dict | None = None
    displayName: str | None = None


@app.post("/gitlab/action", response_model=AgentTaskResponse)
def execute_gitlab_action(req: AgentTaskRequest) -> AgentTaskResponse:
    """
    Mirrors the execute() switch in gitlab.js.
    Calls GitLab REST API v4 with Bearer token.
    """
    action = req.action
    token = req.access_token

    if not token:
        return AgentTaskResponse(
            status="failed",
            error="GitLab access token is missing. Please connect your GitLab account.",
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        # ── list_projects ─────────────────────────────────────────────────────
        if action == "list_projects":
            # GET /projects — mirrors JS: membership=true, order_by=updated_at
            params = {
                "membership": "true",
                "order_by": "updated_at",
                "per_page": req.limit or 10,
            }
            if req.search:
                params["search"] = req.search

            res = requests.get(f"{GITLAB_API}/projects", headers=headers, params=params, timeout=10)
            res.raise_for_status()
            projects = [
                {
                    "id": p.get("id"),
                    "name": p.get("name_with_namespace"),
                    "description": p.get("description"),
                    "private": p.get("visibility") == "private",
                    "url": p.get("web_url"),
                    "stars": p.get("star_count"),
                    "updatedAt": p.get("last_activity_at"),
                }
                for p in res.json()
            ]
            return AgentTaskResponse(
                status="success",
                type="gitlab_list",
                message=f"Found {len(projects)} project(s).",
                data={"projects": projects},
                displayName="GitLab Projects",
            )

        # ── get_issue ─────────────────────────────────────────────────────────
        elif action == "get_issue":
            if not req.projectId or not req.issueIid:
                return AgentTaskResponse(
                    status="failed", error="projectId and issueIid are required."
                )
            # GitLab requires URL-encoded project path if string — mirrors JS encodeURIComponent
            encoded_project = quote(str(req.projectId), safe="")
            res = requests.get(
                f"{GITLAB_API}/projects/{encoded_project}/issues/{req.issueIid}",
                headers=headers,
                timeout=10,
            )
            res.raise_for_status()
            d = res.json()
            return AgentTaskResponse(
                status="success",
                type="gitlab_issue",
                message=f"Issue #{d.get('iid')}: {d.get('title')}",
                data={
                    "id": d.get("id"),
                    "iid": d.get("iid"),
                    "title": d.get("title"),
                    "state": d.get("state"),
                    "author": (d.get("author") or {}).get("username"),
                    "description": d.get("description"),
                    "url": d.get("web_url"),
                    "createdAt": d.get("created_at"),
                },
                displayName=d.get("title"),
            )

        # ── create_issue ──────────────────────────────────────────────────────
        elif action == "create_issue":
            if not req.projectId or not req.title:
                return AgentTaskResponse(
                    status="failed", error="projectId and title are required."
                )
            encoded_project = quote(str(req.projectId), safe="")
            payload = {
                "title": req.title,
                "description": req.description or "",
            }
            res = requests.post(
                f"{GITLAB_API}/projects/{encoded_project}/issues",
                headers=headers,
                json=payload,
                timeout=15,
            )
            res.raise_for_status()
            d = res.json()
            return AgentTaskResponse(
                status="success",
                type="gitlab_action",
                message=f"Issue #{d.get('iid')} created successfully: {d.get('web_url')}",
                data={
                    "issueIid": d.get("iid"),
                    "title": d.get("title"),
                    "url": d.get("web_url"),
                },
                displayName=d.get("title"),
            )

        else:
            return AgentTaskResponse(status="failed", error=f"Unknown action: {action}")

    except requests.exceptions.HTTPError as e:
        logger.exception("GitLab API HTTP error")
        return AgentTaskResponse(status="failed", error=f"GitLab API error: {e.response.text}")
    except Exception as e:
        logger.exception("GitLab agent error")
        return AgentTaskResponse(status="failed", error=str(e))


@app.get("/health")
def health():
    return {"status": "healthy", "agent": "gitlab-agent"}
