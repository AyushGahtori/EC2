"""
api/server.py — Freshdesk Agent
Converted from: marketplace copy/backend/tools/freshdesk.js

Auth: API Key (passed as access_token — Freshdesk uses Basic auth with the key)
Note: The original JS uses mock/placeholder data for most endpoints.
      We implement the real Freshdesk Harvest API calls here.
"""
from __future__ import annotations

import base64
import logging

import requests
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

load_dotenv()
logger = logging.getLogger(__name__)

app = FastAPI(title="Freshdesk Agent API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AgentTaskRequest(BaseModel):
    taskId: str
    userId: str
    agentId: str
    action: str
    # Freshdesk uses API key auth (stored as access_token) + subdomain
    access_token: str | None = None   # Freshdesk API key
    domain: str | None = None         # e.g. "mycompany" → mycompany.freshdesk.com
    # create_ticket
    subject: str | None = None
    description: str | None = None
    status: int | None = None         # 2=Open, 3=Pending, 4=Resolved, 5=Closed
    priority: int | None = None       # 1=Low, 2=Medium, 3=High, 4=Urgent
    # check_ticket_status
    ticket_id: int | None = None
    # search_solutions
    keyword: str | None = None
    # list_tickets
    limit: int | None = None
    model_config = ConfigDict(extra="allow")


class AgentTaskResponse(BaseModel):
    status: str
    type: str | None = None
    error: str | None = None
    message: str | None = None
    data: dict | None = None
    displayName: str | None = None


def _freshdesk_headers(api_key: str) -> dict:
    """
    Freshdesk uses HTTP Basic Auth: base64(api_key + ':')
    Mirrors the JS: Buffer.from(apiKey + ':').toString('base64')
    """
    encoded = base64.b64encode(f"{api_key}:".encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
    }


@app.post("/freshdesk/action", response_model=AgentTaskResponse)
def execute_freshdesk_action(req: AgentTaskRequest) -> AgentTaskResponse:
    """
    Mirrors the execute() switch in freshdesk.js.
    Calls the real Freshdesk REST API.
    """
    action = req.action
    api_key = req.access_token

    if not api_key:
        return AgentTaskResponse(
            status="failed",
            error="Freshdesk API key is missing. Please connect your Freshdesk account.",
        )

    # Freshdesk requires the subdomain, e.g. "mycompany" → mycompany.freshdesk.com
    subdomain = req.domain or "your-account"
    base_url = f"https://{subdomain}.freshdesk.com/api/v2"
    headers = _freshdesk_headers(api_key)

    try:
        # ── create_ticket ─────────────────────────────────────────────────────
        if action == "create_ticket":
            if not req.subject or not req.description:
                return AgentTaskResponse(
                    status="failed", error="subject and description are required."
                )
            payload = {
                "subject": req.subject,
                "description": req.description,
                "status": req.status or 2,      # 2 = Open
                "priority": req.priority or 1,  # 1 = Low
            }
            res = requests.post(f"{base_url}/tickets", headers=headers, json=payload, timeout=15)
            res.raise_for_status()
            ticket = res.json()
            return AgentTaskResponse(
                status="success",
                type="freshdesk_action",
                message=f"Ticket #{ticket.get('id')} created successfully in Freshdesk.",
                data={"ticket_id": ticket.get("id"), "subject": ticket.get("subject")},
                displayName=req.subject,
            )

        # ── check_ticket_status ───────────────────────────────────────────────
        elif action == "check_ticket_status":
            if not req.ticket_id:
                return AgentTaskResponse(status="failed", error="ticket_id is required.")
            res = requests.get(f"{base_url}/tickets/{req.ticket_id}", headers=headers, timeout=10)
            res.raise_for_status()
            ticket = res.json()
            STATUS_MAP = {2: "Open", 3: "Pending", 4: "Resolved", 5: "Closed"}
            return AgentTaskResponse(
                status="success",
                type="freshdesk_status",
                message=f"Ticket #{req.ticket_id} is {STATUS_MAP.get(ticket.get('status'), 'Unknown')}.",
                data={
                    "ticket_id": ticket.get("id"),
                    "subject": ticket.get("subject"),
                    "status": STATUS_MAP.get(ticket.get("status"), ticket.get("status")),
                    "created_at": ticket.get("created_at"),
                    "description": ticket.get("description_text"),
                },
                displayName=f"Ticket #{req.ticket_id}",
            )

        # ── search_solutions ──────────────────────────────────────────────────
        elif action == "search_solutions":
            if not req.keyword:
                return AgentTaskResponse(status="failed", error="keyword is required.")
            res = requests.get(
                f"{base_url}/search/solutions",
                headers=headers,
                params={"term": req.keyword},
                timeout=10,
            )
            res.raise_for_status()
            results = res.json()
            articles = results if isinstance(results, list) else results.get("results", [])
            return AgentTaskResponse(
                status="success",
                type="freshdesk_list",
                message=f"Found {len(articles)} solution(s) for '{req.keyword}'.",
                data={"results": articles},
                displayName="Knowledge Base",
            )

        # ── list_tickets ──────────────────────────────────────────────────────
        elif action == "list_tickets":
            limit = req.limit or 5
            res = requests.get(
                f"{base_url}/tickets",
                headers=headers,
                params={"per_page": limit},
                timeout=10,
            )
            res.raise_for_status()
            tickets = res.json()
            STATUS_MAP = {2: "open", 3: "pending", 4: "resolved", 5: "closed"}
            clean = [
                {
                    "ticket_id": t.get("id"),
                    "subject": t.get("subject"),
                    "status": STATUS_MAP.get(t.get("status"), str(t.get("status"))),
                    "priority": t.get("priority"),
                    "updated_at": t.get("updated_at"),
                }
                for t in tickets[:limit]
            ]
            return AgentTaskResponse(
                status="success",
                type="freshdesk_list",
                message=f"Showing {len(clean)} ticket(s).",
                data={"tickets": clean},
                displayName="Support Tickets",
            )

        else:
            return AgentTaskResponse(status="failed", error=f"Unknown action: {action}")

    except requests.exceptions.HTTPError as e:
        logger.exception("Freshdesk API HTTP error")
        return AgentTaskResponse(status="failed", error=f"Freshdesk API error: {e.response.text}")
    except Exception as e:
        logger.exception("Freshdesk agent error")
        return AgentTaskResponse(status="failed", error=str(e))


@app.get("/health")
def health():
    return {"status": "healthy", "agent": "freshdesk-agent"}
