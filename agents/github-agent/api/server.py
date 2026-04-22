"""
api/server.py — GitHub Agent
Converted from: marketplace copy/backend/tools/github.js

Auth: OAuth2 (GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET)
Scopes: repo, read:user, user:email
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import requests
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

app = FastAPI(title="GitHub Agent API", version="1.0.0")
apply_api_security(app)

GITHUB_API = "https://api.github.com"


class AgentTaskRequest(BaseModel):
    taskId: str
    userId: str
    agentId: str
    action: str
    access_token: str | None = None
    # list_repositories / search_repositories
    limit: int | None = None
    sort: str | None = None
    query: str | None = None
    # get_issue / create_issue
    owner: str | None = None
    repo: str | None = None
    issueNumber: int | None = None
    title: str | None = None
    body: str | None = None
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


@app.post("/github/action", response_model=AgentTaskResponse)
def execute_github_action(req: AgentTaskRequest) -> AgentTaskResponse:
    """
    Mirrors the execute() switch in github.js.
    Calls the GitHub REST API v3 using the user's OAuth2 Bearer token.
    """
    action = req.action
    credentials = resolve_provider_credentials(
        user_id=req.userId,
        provider="github",
        access_token=req.access_token,
    )
    token = credentials.get("access_token")

    if not token:
        return AgentTaskResponse(
            **auth_required_response(
                agent_slug="github",
                agent_id="github-agent",
                provider="github",
                message="GitHub access token is missing. Please connect your GitHub account.",
            )
        )

    # Mirrors JS headers exactly: Authorization, Accept, X-GitHub-Api-Version
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        # ── list_repositories ─────────────────────────────────────────────────
        if action == "list_repositories":
            # GET /user/repos
            params = {
                "sort": req.sort or "updated",
                "per_page": req.limit or 10,
            }
            res = requests.get(f"{GITHUB_API}/user/repos", headers=headers, params=params, timeout=10)
            res.raise_for_status()
            repos = [
                {
                    "name": r.get("full_name"),
                    "description": r.get("description"),
                    "private": r.get("private"),
                    "url": r.get("html_url"),
                    "language": r.get("language"),
                    "stars": r.get("stargazers_count"),
                    "updatedAt": r.get("updated_at"),
                }
                for r in res.json()
            ]
            return AgentTaskResponse(
                status="success",
                type="github_list",
                message=f"Found {len(repos)} repository(ies).",
                data={"repositories": repos},
                displayName="GitHub Repositories",
            )

        # ── search_repositories ───────────────────────────────────────────────
        elif action == "search_repositories":
            if not req.query:
                return AgentTaskResponse(status="failed", error="query is required.")
            # GET /search/repositories
            res = requests.get(
                f"{GITHUB_API}/search/repositories",
                headers=headers,
                params={"q": req.query, "per_page": 5},
                timeout=10,
            )
            res.raise_for_status()
            data = res.json()
            results = [
                {
                    "name": r.get("full_name"),
                    "description": r.get("description"),
                    "url": r.get("html_url"),
                    "language": r.get("language"),
                    "stars": r.get("stargazers_count"),
                }
                for r in (data.get("items") or [])
            ]
            return AgentTaskResponse(
                status="success",
                type="github_list",
                message=f"Found {data.get('total_count', 0)} result(s) for '{req.query}'.",
                data={"totalCount": data.get("total_count"), "results": results},
                displayName="Repository Search",
            )

        # ── get_issue ─────────────────────────────────────────────────────────
        elif action == "get_issue":
            if not req.owner or not req.repo or not req.issueNumber:
                return AgentTaskResponse(
                    status="failed",
                    error="owner, repo, and issueNumber are all required.",
                )
            # GET /repos/{owner}/{repo}/issues/{issue_number}
            res = requests.get(
                f"{GITHUB_API}/repos/{req.owner}/{req.repo}/issues/{req.issueNumber}",
                headers=headers,
                timeout=10,
            )
            res.raise_for_status()
            d = res.json()
            return AgentTaskResponse(
                status="success",
                type="github_issue",
                message=f"Issue #{req.issueNumber}: {d.get('title')}",
                data={
                    "title": d.get("title"),
                    "state": d.get("state"),
                    "author": (d.get("user") or {}).get("login"),
                    "body": d.get("body"),
                    "comments": d.get("comments"),
                    "url": d.get("html_url"),
                    "createdAt": d.get("created_at"),
                },
                displayName=d.get("title"),
            )

        # ── create_issue ──────────────────────────────────────────────────────
        elif action == "create_issue":
            if not req.owner or not req.repo or not req.title:
                return AgentTaskResponse(
                    status="failed",
                    error="owner, repo, and title are required.",
                )
            # POST /repos/{owner}/{repo}/issues
            payload = {"title": req.title, "body": req.body or ""}
            res = requests.post(
                f"{GITHUB_API}/repos/{req.owner}/{req.repo}/issues",
                headers=headers,
                json=payload,
                timeout=15,
            )
            res.raise_for_status()
            d = res.json()
            return AgentTaskResponse(
                status="success",
                type="github_action",
                message=f"Issue #{d.get('number')} created successfully: {d.get('html_url')}",
                data={
                    "issueNumber": d.get("number"),
                    "title": d.get("title"),
                    "url": d.get("html_url"),
                },
                displayName=d.get("title"),
            )

        else:
            return AgentTaskResponse(status="failed", error=f"Unknown action: {action}")

    except requests.exceptions.HTTPError as e:
        logger.exception("GitHub API HTTP error")
        return AgentTaskResponse(status="failed", error=f"GitHub API error: {e.response.text}")
    except Exception as e:
        logger.exception("GitHub agent error")
        return AgentTaskResponse(status="failed", error=str(e))


@app.get("/health")
def health():
    return {"status": "healthy", "agent": "github-agent"}
