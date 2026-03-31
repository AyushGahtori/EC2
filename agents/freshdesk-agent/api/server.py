"""
Freshdesk agent API.

Matches the current JS tool behavior, which returns stub/mock payloads rather
than making live API calls.
"""

from __future__ import annotations

import os
import random
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

load_dotenv()

app = FastAPI(title="Freshdesk Agent API", version="1.0.0")
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
    domain: str | None = None
    subject: str | None = None
    description: str | None = None
    status: int | None = None
    priority: int | None = None
    ticket_id: int | None = None
    keyword: str | None = None
    limit: int | None = None
    model_config = ConfigDict(extra="allow")


class AgentTaskResponse(BaseModel):
    status: str
    type: str | None = None
    error: str | None = None
    message: str | None = None
    data: dict | None = None
    displayName: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.post("/freshdesk/action", response_model=AgentTaskResponse)
def execute_freshdesk_action(req: AgentTaskRequest) -> AgentTaskResponse:
    action = req.action
    api_key = req.access_token or os.getenv("FRESHDESK_API_KEY")
    account = req.domain or os.getenv("FRESHDESK_DOMAIN") or "your-account"

    if not api_key:
        return AgentTaskResponse(
            status="failed",
            error="Freshdesk API key is missing. Configure FRESHDESK_API_KEY or provide access_token.",
        )

    if action == "create_ticket":
        if not req.subject or not req.description:
            return AgentTaskResponse(
                status="failed",
                error="subject and description are required.",
            )
        ticket_id = random.randint(10000, 50000)
        return AgentTaskResponse(
            status="success",
            type="freshdesk_action",
            message="Ticket created successfully in Freshdesk",
            displayName=req.subject,
            data={
                "success": True,
                "ticket_id": ticket_id,
                "subject": req.subject,
                "status": "Open",
                "message": "Ticket created successfully in Freshdesk",
            },
        )

    if action == "check_ticket_status":
        if not req.ticket_id:
            return AgentTaskResponse(status="failed", error="ticket_id is required.")
        return AgentTaskResponse(
            status="success",
            type="freshdesk_status",
            message=f"Ticket {req.ticket_id} status fetched.",
            displayName=f"Ticket {req.ticket_id}",
            data={
                "ticket_id": req.ticket_id,
                "status": "Pending",
                "created_at": _now_iso(),
                "description": "Awaiting customer reply.",
            },
        )

    if action == "search_solutions":
        if not req.keyword:
            return AgentTaskResponse(status="failed", error="keyword is required.")
        return AgentTaskResponse(
            status="success",
            type="freshdesk_list",
            message=f"Found solutions for '{req.keyword}'.",
            displayName="Knowledge Base",
            data={
                "results": [
                    {
                        "id": 202,
                        "title": f"Guide for: {req.keyword}",
                        "url": f"https://{account}.freshdesk.com/support/solutions/articles/202",
                    }
                ]
            },
        )

    if action == "list_tickets":
        limit = req.limit or 5
        tickets = [
            {
                "ticket_id": random.randint(10000, 99999),
                "subject": "Cannot access account",
                "status": "open",
                "priority": "high",
                "updated_at": _now_iso(),
            },
            {
                "ticket_id": random.randint(10000, 99999),
                "subject": "Payment failed",
                "status": "pending",
                "priority": "urgent",
                "updated_at": _now_iso(),
            },
            {
                "ticket_id": random.randint(10000, 99999),
                "subject": "General question",
                "status": "closed",
                "priority": "low",
                "updated_at": _now_iso(),
            },
        ][:limit]
        return AgentTaskResponse(
            status="success",
            type="freshdesk_list",
            message=f"Showing {len(tickets)} ticket(s).",
            displayName="Support Tickets",
            data={"tickets": tickets},
        )

    return AgentTaskResponse(status="failed", error=f"Unknown action: {action}")


@app.get("/health")
def health():
    return {"status": "healthy", "agent": "freshdesk-agent"}
