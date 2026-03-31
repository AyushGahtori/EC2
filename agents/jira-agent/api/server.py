"""
Jira agent API.

Matches the current JS tool behavior, which exposes Jira actions but returns
stub/mock responses instead of live Atlassian API calls.
"""

from __future__ import annotations

import random
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ec2_shared.agent_runtime import auth_required_response, resolve_provider_credentials

load_dotenv()

app = FastAPI(title="Jira Agent API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AgentTaskRequest(BaseModel):
    taskId: str | None = None
    userId: str | None = None
    agentId: str | None = None
    action: str
    access_token: str | None = None
    project_key: str | None = None
    summary: str | None = None
    description: str | None = None
    issue_type: str | None = None
    issue_key: str | None = None
    jql: str | None = None
    limit: int | None = None
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.post("/jira/action", response_model=AgentTaskResponse)
def execute_jira_action(req: AgentTaskRequest) -> AgentTaskResponse:
    credentials = resolve_provider_credentials(
        user_id=req.userId,
        provider="atlassian",
        access_token=req.access_token,
    )
    if not credentials.get("access_token"):
        return AgentTaskResponse(
            **auth_required_response(
                agent_slug="jira",
                agent_id="jira-agent",
                provider="atlassian",
                message="Jira access token is missing. Please connect your Jira account.",
            )
        )

    action = req.action

    if action == "create_issue":
        if not req.project_key or not req.summary or not req.description:
            return AgentTaskResponse(
                status="failed",
                error="project_key, summary, and description are required.",
            )
        issue_key = f"{req.project_key}-{random.randint(100, 999)}"
        return AgentTaskResponse(
            status="success",
            type="jira_action",
            message="Issue created successfully in Jira",
            displayName=issue_key,
            data={
                "success": True,
                "issue_key": issue_key,
                "summary": req.summary,
                "type": req.issue_type or "Task",
                "message": "Issue created successfully in Jira",
            },
        )

    if action == "get_issue_status":
        if not req.issue_key:
            return AgentTaskResponse(status="failed", error="issue_key is required.")
        return AgentTaskResponse(
            status="success",
            type="jira_status",
            message=f"Fetched status for {req.issue_key}.",
            displayName=req.issue_key,
            data={
                "issue_key": req.issue_key,
                "status": "In Progress",
                "assignee": "Unassigned",
                "last_updated": _now_iso(),
            },
        )

    if action == "search_issues":
        if not req.jql:
            return AgentTaskResponse(status="failed", error="jql is required.")
        return AgentTaskResponse(
            status="success",
            type="jira_list",
            message="Found issues matching the JQL query.",
            displayName="Jira Search",
            data={
                "total": 1,
                "issues": [
                    {
                        "key": "PROJ-101",
                        "summary": "Example search result",
                        "status": "Done",
                    }
                ],
            },
        )

    if action == "list_issues":
        limit = req.limit or 5
        issues = [
            {
                "key": f"PROJ-{index}",
                "summary": f"Example assigned issue {index}",
                "status": "To Do" if index % 2 else "In Progress",
            }
            for index in range(101, 101 + limit)
        ]
        return AgentTaskResponse(
            status="success",
            type="jira_list",
            message=f"Showing {len(issues)} issue(s).",
            displayName="My Jira Issues",
            data={"issues": issues},
        )

    return AgentTaskResponse(status="failed", error=f"Unknown action: {action}")


@app.get("/health")
def health():
    return {"status": "healthy", "agent": "jira-agent"}
