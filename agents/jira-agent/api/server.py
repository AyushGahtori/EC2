"""
api/server.py — Jira Agent
Converted from: marketplace copy/backend/tools/jira.js

Auth: OAuth2 via Atlassian (JIRA_CLIENT_ID / JIRA_CLIENT_SECRET)
Scopes: read:jira-work, write:jira-work, read:jira-user

NOTE: The original JS uses mock/stub data for create_issue and search_issues.
      We implement the real Atlassian Jira Cloud REST API calls here.
"""
from __future__ import annotations

import logging

import requests
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

load_dotenv()
logger = logging.getLogger(__name__)

app = FastAPI(title="Jira Agent API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Atlassian Jira Cloud REST API base — requires cloudId from Atlassian
# We first fetch the cloudId via the accessible-resources endpoint.
ATLASSIAN_AUTH = "https://auth.atlassian.com"
ATLASSIAN_RESOURCES = "https://api.atlassian.com/oauth/token/accessible-resources"


class AgentTaskRequest(BaseModel):
    taskId: str
    userId: str
    agentId: str
    action: str
    access_token: str | None = None
    # create_issue
    project_key: str | None = None
    summary: str | None = None
    description: str | None = None
    issue_type: str | None = None    # 'Bug', 'Task', 'Story', etc.
    # get_issue_status
    issue_key: str | None = None    # e.g. "PROJ-123"
    # search_issues
    jql: str | None = None
    # list_issues
    limit: int | None = None
    model_config = ConfigDict(extra="allow")


class AgentTaskResponse(BaseModel):
    status: str
    type: str | None = None
    error: str | None = None
    message: str | None = None
    data: dict | None = None
    displayName: str | None = None


def _get_cloud_id(token: str) -> str:
    """
    Fetch the Atlassian Cloud ID for the user's first accessible Jira instance.
    Required to build the correct API base URL.
    """
    res = requests.get(
        ATLASSIAN_RESOURCES,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=10,
    )
    res.raise_for_status()
    resources = res.json()
    if not resources:
        raise ValueError("No Jira Cloud instances found for this account.")
    return resources[0]["id"]


@app.post("/jira/action", response_model=AgentTaskResponse)
def execute_jira_action(req: AgentTaskRequest) -> AgentTaskResponse:
    """
    Mirrors the execute() switch in jira.js, using the real Atlassian Jira Cloud REST API v3.
    """
    action = req.action
    token = req.access_token

    if not token:
        return AgentTaskResponse(
            status="failed",
            error="Jira (Atlassian) access token is missing. Please connect your Jira account.",
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        # Dynamically resolve the cloud ID and API base for this user
        cloud_id = _get_cloud_id(token)
        JIRA_API = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3"

        # ── create_issue ──────────────────────────────────────────────────────
        if action == "create_issue":
            if not req.project_key or not req.summary or not req.description:
                return AgentTaskResponse(
                    status="failed",
                    error="project_key, summary, and description are required.",
                )
            payload = {
                "fields": {
                    "project": {"key": req.project_key},
                    "summary": req.summary,
                    "description": {
                        "type": "doc",
                        "version": 1,
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": req.description}],
                            }
                        ],
                    },
                    "issuetype": {"name": req.issue_type or "Task"},
                }
            }
            res = requests.post(f"{JIRA_API}/issue", headers=headers, json=payload, timeout=15)
            res.raise_for_status()
            d = res.json()
            return AgentTaskResponse(
                status="success",
                type="jira_action",
                message=f"Issue {d.get('key')} created successfully in Jira.",
                data={
                    "issue_key": d.get("key"),
                    "id": d.get("id"),
                    "url": f"https://atlassian.net/browse/{d.get('key')}",
                },
                displayName=d.get("key"),
            )

        # ── get_issue_status ──────────────────────────────────────────────────
        elif action == "get_issue_status":
            if not req.issue_key:
                return AgentTaskResponse(status="failed", error="issue_key is required.")
            res = requests.get(
                f"{JIRA_API}/issue/{req.issue_key}",
                headers=headers,
                timeout=10,
            )
            res.raise_for_status()
            d = res.json()
            fields = d.get("fields", {})
            return AgentTaskResponse(
                status="success",
                type="jira_status",
                message=f"{d.get('key')}: {fields.get('summary')} — {fields.get('status', {}).get('name')}",
                data={
                    "issue_key": d.get("key"),
                    "summary": fields.get("summary"),
                    "status": fields.get("status", {}).get("name"),
                    "assignee": (fields.get("assignee") or {}).get("displayName", "Unassigned"),
                    "last_updated": fields.get("updated"),
                },
                displayName=d.get("key"),
            )

        # ── search_issues ─────────────────────────────────────────────────────
        elif action == "search_issues":
            if not req.jql:
                return AgentTaskResponse(status="failed", error="jql query string is required.")
            res = requests.get(
                f"{JIRA_API}/search",
                headers=headers,
                params={"jql": req.jql, "maxResults": 10},
                timeout=10,
            )
            res.raise_for_status()
            d = res.json()
            issues = [
                {
                    "key": i.get("key"),
                    "summary": (i.get("fields") or {}).get("summary"),
                    "status": ((i.get("fields") or {}).get("status") or {}).get("name"),
                }
                for i in (d.get("issues") or [])
            ]
            return AgentTaskResponse(
                status="success",
                type="jira_list",
                message=f"Found {d.get('total', len(issues))} issue(s) matching your query.",
                data={"total": d.get("total"), "issues": issues},
                displayName="Jira Search",
            )

        # ── list_issues ───────────────────────────────────────────────────────
        elif action == "list_issues":
            limit = req.limit or 5
            # List issues assigned to the current user
            res = requests.get(
                f"{JIRA_API}/search",
                headers=headers,
                params={"jql": "assignee = currentUser() ORDER BY updated DESC", "maxResults": limit},
                timeout=10,
            )
            res.raise_for_status()
            d = res.json()
            issues = [
                {
                    "key": i.get("key"),
                    "summary": (i.get("fields") or {}).get("summary"),
                    "status": ((i.get("fields") or {}).get("status") or {}).get("name"),
                    "priority": ((i.get("fields") or {}).get("priority") or {}).get("name"),
                }
                for i in (d.get("issues") or [])[:limit]
            ]
            return AgentTaskResponse(
                status="success",
                type="jira_list",
                message=f"Showing {len(issues)} assigned issue(s).",
                data={"issues": issues},
                displayName="My Jira Issues",
            )

        else:
            return AgentTaskResponse(status="failed", error=f"Unknown action: {action}")

    except requests.exceptions.HTTPError as e:
        logger.exception("Jira API HTTP error")
        return AgentTaskResponse(status="failed", error=f"Jira API error: {e.response.text}")
    except Exception as e:
        logger.exception("Jira agent error")
        return AgentTaskResponse(status="failed", error=str(e))


@app.get("/health")
def health():
    return {"status": "healthy", "agent": "jira-agent"}
