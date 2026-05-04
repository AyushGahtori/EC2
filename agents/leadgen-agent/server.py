from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
try:
    from pydantic import ConfigDict
except Exception:  # pragma: no cover - pydantic v1 fallback
    ConfigDict = None  # type: ignore
from fastapi.responses import HTMLResponse

ROOT = Path(__file__).resolve().parent
EC2_ROOT = ROOT.parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(EC2_ROOT))

from ec2_shared.agent_response import as_text, card, failed, needs_input, require_fields, success
from ec2_shared.ui import render_agent_window

AGENT_ID = "leadgen-agent"
AGENT_NAME = "LeadGen Agent"


class ActionRequest(BaseModel):
    action: str | None = None
    if ConfigDict:
        model_config = ConfigDict(extra="allow")
    else:
        class Config:
            extra = "allow"


def _payload(model: ActionRequest) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _session_id(payload: dict[str, Any]) -> str:
    return as_text(payload.get("session_id") or payload.get("sessionId") or payload.get("chatId")) or str(uuid.uuid4())


async def _run_agent(payload: dict[str, Any], action: str) -> dict[str, Any]:
    prompt = as_text(payload.get("prompt") or payload.get("message") or payload.get("query") or payload.get("parameters"))
    if not prompt:
        return needs_input(
            agent=AGENT_ID,
            action=action,
            message="Tell LeadGen what market, role, location, or company profile to research.",
            missing_fields=["prompt"],
        )

    from app.agent.graph import run_agent
    from app.services.redis_client import conversation_append, conversation_get

    session_id = _session_id(payload)
    await conversation_append(session_id, "user", prompt)
    history = await conversation_get(session_id)
    chunks: list[str] = []
    async for chunk in run_agent(query=prompt, session_id=session_id, conversation_history=history[:-1]):
        chunks.append(str(chunk))
    response = "".join(chunks).strip()
    if response:
        await conversation_append(session_id, "assistant", response)

    return success(
        agent=AGENT_ID,
        action=action,
        summary=response or "Lead generation run completed.",
        result={"session_id": session_id, "response": response},
        cards=[
            card("LeadGen response", response or "Completed.", {"session_id": session_id}),
            card("Session", "Use this ID to fetch stored leads or continue the search.", {"session_id": session_id}),
        ],
        logs=["Ran the copied LangGraph lead generation flow.", "Conversation history was persisted when Redis was available."],
        next_actions=["List saved leads", "Continue this LeadGen session", "Clear this session when finished"],
    )


async def _list_leads(payload: dict[str, Any], action: str) -> dict[str, Any]:
    from app.services.mongodb_client import count_leads, get_leads

    session_id = as_text(payload.get("session_id") or payload.get("sessionId")) or None
    limit = int(payload.get("limit") or 100)
    skip = int(payload.get("skip") or 0)
    leads = await get_leads(session_id=session_id, limit=max(1, min(limit, 500)), skip=max(0, skip))
    total = await count_leads(session_id=session_id)
    return success(
        agent=AGENT_ID,
        action=action,
        summary=f"Found {len(leads)} lead records{f' for session {session_id}' if session_id else ''}.",
        result={"leads": leads, "total": total, "session_id": session_id, "limit": limit, "skip": skip},
        cards=[
            card("Lead records", f"{len(leads)} records returned.", {"total": total, "session_id": session_id or "all"}),
            *[
                card(
                    as_text(item.get("name") or item.get("company") or item.get("title") or "Lead"),
                    as_text(item.get("description") or item.get("email") or item.get("url")),
                    {k: v for k, v in item.items() if k not in {"_id"} and v is not None},
                )
                for item in leads[:5]
                if isinstance(item, dict)
            ],
        ],
        next_actions=["Export or inspect lead details", "Run another targeted search"],
    )


async def _history(payload: dict[str, Any], action: str) -> dict[str, Any]:
    missing = require_fields(AGENT_ID, action, payload, ["session_id"])
    if missing:
        return missing
    from app.services.redis_client import conversation_get

    session_id = _session_id(payload)
    history = await conversation_get(session_id)
    return success(
        agent=AGENT_ID,
        action=action,
        summary=f"Loaded {len(history)} conversation messages.",
        result={"session_id": session_id, "history": history},
        cards=[card("Conversation history", f"{len(history)} messages found.", {"session_id": session_id})],
    )


async def _clear(payload: dict[str, Any], action: str) -> dict[str, Any]:
    missing = require_fields(AGENT_ID, action, payload, ["session_id"])
    if missing:
        return missing
    from app.services.redis_client import conversation_clear

    session_id = _session_id(payload)
    await conversation_clear(session_id)
    return success(
        agent=AGENT_ID,
        action=action,
        summary=f"Cleared LeadGen conversation history for {session_id}.",
        result={"session_id": session_id, "cleared": True},
        cards=[card("Session cleared", "Conversation memory was cleared.", {"session_id": session_id})],
    )


CAPABILITIES = [
    {"name": "run_leadgen", "label": "Run LeadGen", "description": "Find, enrich, score, and save leads from a natural-language request.", "required": ["prompt"], "optional": ["session_id"]},
    {"name": "list_leads", "label": "List Leads", "description": "Load persisted leads from MongoDB, optionally scoped to a session.", "required": [], "optional": ["session_id", "limit", "skip"]},
    {"name": "get_history", "label": "Get History", "description": "Load Redis-backed conversation history for a session.", "required": ["session_id"], "optional": []},
    {"name": "clear_session", "label": "Clear Session", "description": "Clear conversation memory for one LeadGen session.", "required": ["session_id"], "optional": []},
    {"name": "new_session", "label": "New Session", "description": "Create a new session id.", "required": [], "optional": []},
    {"name": "list_capabilities", "label": "List Capabilities", "description": "Show LeadGen capabilities.", "required": [], "optional": []},
]

UI_SPEC = {
    "name": AGENT_NAME,
    "description": "Autonomous lead generation with search, company enrichment, email discovery, scoring, storage, and session memory.",
    "endpoint": "/leadgen/action",
    "actions": CAPABILITIES,
    "examples": [
        "Find 25 B2B SaaS founders in Bengaluru who recently raised seed funding.",
        "Find decision makers at logistics companies in the US and score the strongest leads.",
        "Continue my last search and focus only on companies with public emails.",
    ],
    "scope": [
        "Search and enrichment happen through the copied LangGraph agent.",
        "Stored leads and session history stay in EC2-side MongoDB and Redis.",
        "The adapter returns chat-safe cards instead of raw JSON dumps.",
    ],
    "usage": [
        "Use run_leadgen for natural-language lead research.",
        "Use list_leads after a run to inspect saved records.",
        "Use get_history or clear_session for session maintenance.",
    ],
}

app = FastAPI(title=AGENT_NAME, version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/", response_class=HTMLResponse)
async def root() -> str:
    return render_agent_window(UI_SPEC)


@app.get("/leadgen/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "agent": AGENT_ID, "actions": [item["name"] for item in CAPABILITIES]}


@app.post("/leadgen/action")
async def action(request: ActionRequest) -> dict[str, Any]:
    payload = _payload(request)
    selected = as_text(payload.get("action") or "run_leadgen")
    try:
        if selected in {"run_leadgen", "search_leads", "generate_leads", "chat"}:
            return await _run_agent(payload, selected)
        if selected == "list_leads":
            return await _list_leads(payload, selected)
        if selected == "get_history":
            return await _history(payload, selected)
        if selected in {"clear_session", "reset_session"}:
            return await _clear(payload, selected)
        if selected == "new_session":
            session_id = str(uuid.uuid4())
            return success(agent=AGENT_ID, action=selected, summary="Created a new LeadGen session.", result={"session_id": session_id}, cards=[card("New session", "Use this ID for the next run.", {"session_id": session_id})])
        if selected == "list_capabilities":
            return success(agent=AGENT_ID, action=selected, summary="LeadGen capabilities loaded.", result={"actions": CAPABILITIES}, cards=[card("Capabilities", "LeadGen can research, enrich, score, store, and retrieve leads.", {"actions": ", ".join(item["name"] for item in CAPABILITIES)})])
        return needs_input(agent=AGENT_ID, action=selected, message=f"LeadGen does not expose the action '{selected}'.", missing_fields=["action"])
    except Exception as exc:
        return failed(agent=AGENT_ID, action=selected, public_message="LeadGen could not complete this request yet.", error=exc)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8050)
