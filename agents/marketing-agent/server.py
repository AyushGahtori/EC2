from __future__ import annotations

import base64
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
try:
    from pydantic import ConfigDict
except Exception:  # pragma: no cover
    ConfigDict = None  # type: ignore

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "marketing-agent" / "backend"
EC2_ROOT = ROOT.parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(EC2_ROOT))

from ec2_shared.agent_response import as_text, card, failed, needs_input, require_fields, success
from ec2_shared.ui import render_agent_window

AGENT_ID = "marketing-agent"
AGENT_NAME = "Marketing Agent"


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


def _decode_data_url(value: str) -> str:
    if "," in value and value.startswith("data:"):
        return value.split(",", 1)[1]
    return value


async def _ensure_session(payload: dict[str, Any]) -> str:
    from models.schemas import SessionDocument
    from services.mongodb_service import create_session, get_session, update_session

    session_id = as_text(payload.get("session_id") or payload.get("sessionId") or payload.get("chatId"))
    if session_id and await get_session(session_id):
        updates: dict[str, Any] = {}
        if payload.get("product_name"):
            updates["product_name"] = as_text(payload.get("product_name"))
        if payload.get("brand_guidelines") is not None:
            updates["brand_guidelines"] = as_text(payload.get("brand_guidelines"))
        if updates:
            await update_session(session_id, updates)
        return session_id

    session_id = session_id or str(uuid.uuid4())
    now = datetime.utcnow()
    await create_session(
        SessionDocument(
            session_id=session_id,
            name=as_text(payload.get("name") or payload.get("title")) or f"Marketing {now.strftime('%b %d, %H:%M')}",
            product_name=as_text(payload.get("product_name")) or None,
            brand_guidelines=as_text(payload.get("brand_guidelines")) or None,
            created_at=now,
            updated_at=now,
        )
    )
    return session_id


async def _run_marketing(payload: dict[str, Any], action: str) -> dict[str, Any]:
    prompt = as_text(payload.get("prompt") or payload.get("message") or payload.get("query") or payload.get("parameters"))
    if not prompt:
        return needs_input(
            agent=AGENT_ID,
            action=action,
            message="Describe the campaign, product, channel, or creative you want the marketing agent to produce.",
            missing_fields=["prompt"],
        )

    from agents.marketing_agent import get_final_response
    from services.mongodb_service import get_messages
    from services.redis_service import get_cached_product_analysis

    session_id = await _ensure_session(payload)
    raw_history = await get_messages(session_id, limit=20)
    history = [{"role": item.get("role"), "content": item.get("content")} for item in raw_history]
    image_b64 = as_text(payload.get("image_base64") or payload.get("file_data_url"))
    if image_b64:
        image_b64 = _decode_data_url(image_b64)
    product_context = as_text(payload.get("product_context")) or await get_cached_product_analysis(session_id)
    response = await get_final_response(
        session_id=session_id,
        user_message=prompt,
        message_history=history,
        product_image_b64=image_b64 or None,
        product_context=product_context or None,
    )
    summary = as_text(response) or "Marketing content generated."
    return success(
        agent=AGENT_ID,
        action=action,
        summary=summary,
        result={"session_id": session_id, "response": response},
        cards=[
            card("Marketing output", summary, {"session_id": session_id}),
            card("Creative context", "The copied marketing LangGraph handled product analysis, content generation, and persistence.", {"image_provided": bool(image_b64), "product_context": bool(product_context)}),
        ],
        logs=["Ran original marketing agent final-response flow.", "Session state, product context, generated content, MongoDB, and Redis hooks remain preserved."],
        next_actions=["Edit the creative", "Generate variants", "List generated content", "Switch LLM provider"],
    )


async def _list_sessions(payload: dict[str, Any], action: str) -> dict[str, Any]:
    from services.mongodb_service import list_sessions

    limit = int(payload.get("limit") or 50)
    sessions = await list_sessions(limit=max(1, min(limit, 200)))
    return success(
        agent=AGENT_ID,
        action=action,
        summary=f"Loaded {len(sessions)} marketing sessions.",
        result={"sessions": sessions},
        cards=[card("Sessions", f"{len(sessions)} sessions returned.", {"limit": limit})],
    )


async def _history(payload: dict[str, Any], action: str) -> dict[str, Any]:
    missing = require_fields(AGENT_ID, action, payload, ["session_id"])
    if missing:
        return missing
    from services.mongodb_service import get_content, get_messages

    session_id = as_text(payload.get("session_id"))
    messages = await get_messages(session_id, limit=int(payload.get("limit") or 50))
    content = await get_content(session_id, limit=20)
    return success(
        agent=AGENT_ID,
        action=action,
        summary=f"Loaded {len(messages)} messages and {len(content)} generated content items.",
        result={"session_id": session_id, "messages": messages, "content": content},
        cards=[card("Session history", "Marketing history loaded.", {"messages": len(messages), "content_items": len(content)})],
    )


async def _providers(payload: dict[str, Any], action: str) -> dict[str, Any]:
    from services.runtime_config import ALLOWED_PROVIDERS, active_model_for, get_active_provider, provider_is_configured, set_active_provider
    from services.llm_service import clear_llm_cache
    from agents.marketing_agent import reset_agent_graph

    provider = as_text(payload.get("provider"))
    if action == "switch_provider":
        if not provider:
            return needs_input(agent=AGENT_ID, action=action, message="Choose a provider before switching.", missing_fields=["provider"])
        if provider not in ALLOWED_PROVIDERS:
            return needs_input(agent=AGENT_ID, action=action, message=f"Provider '{provider}' is not supported.", missing_fields=["provider"])
        if not provider_is_configured(provider):
            return needs_input(agent=AGENT_ID, action=action, message=f"Provider '{provider}' has no credentials configured.", missing_fields=[f"{provider.upper()}_API_KEY"])
        set_active_provider(provider)
        clear_llm_cache()
        reset_agent_graph()

    active = get_active_provider()
    providers = [
        {"name": name, "configured": provider_is_configured(name), "model": active_model_for(name)}
        for name in sorted(ALLOWED_PROVIDERS)
    ]
    return success(
        agent=AGENT_ID,
        action=action,
        summary=f"Marketing provider is {active}.",
        result={"active": active, "providers": providers},
        cards=[card("LLM providers", f"Active provider: {active}", {item["name"]: f"{item['model']} ({'configured' if item['configured'] else 'not configured'})" for item in providers})],
    )


async def _product(payload: dict[str, Any], action: str) -> dict[str, Any]:
    from models.schemas import ProductDocument
    from services.mongodb_service import get_product, save_product
    from services.redis_service import cache_product_analysis, get_cached_product_analysis

    if action == "save_product_analysis":
        missing = require_fields(AGENT_ID, action, payload, ["session_id", "analysis"])
        if missing:
            return missing
        session_id = as_text(payload.get("session_id"))
        image_id = as_text(payload.get("image_id") or payload.get("file_name")) or "inline-image"
        image_url = as_text(payload.get("image_url") or payload.get("file_data_url")) or image_id
        product_id = await save_product(
            ProductDocument(
                session_id=session_id,
                name=as_text(payload.get("name") or payload.get("product_name")) or None,
                analysis=as_text(payload.get("analysis")),
                image_id=image_id,
                image_url=image_url,
            )
        )
        await cache_product_analysis(session_id, as_text(payload.get("analysis")))
        return success(agent=AGENT_ID, action=action, summary="Product analysis saved.", result={"session_id": session_id, "product_id": product_id}, cards=[card("Product saved", "The analysis is cached for future marketing turns.", {"session_id": session_id})])

    missing = require_fields(AGENT_ID, action, payload, ["session_id"])
    if missing:
        return missing
    session_id = as_text(payload.get("session_id"))
    cached = await get_cached_product_analysis(session_id)
    product = await get_product(session_id)
    return success(
        agent=AGENT_ID,
        action=action,
        summary="Loaded product context." if cached or product else "No product analysis has been saved for this session yet.",
        result={"session_id": session_id, "analysis": cached or (product or {}).get("analysis"), "product": product},
        cards=[card("Product context", cached or (product or {}).get("analysis") or "No saved context yet.", {"session_id": session_id})],
    )


CAPABILITIES = [
    {"name": "run_marketing_agent", "label": "Run Marketing Agent", "description": "Create or edit marketing strategy, copy, posters, campaigns, and product creative.", "required": ["prompt"], "optional": ["session_id", "image_base64", "product_context", "brand_guidelines", "provider"]},
    {"name": "generate_campaign", "label": "Generate Campaign", "description": "Campaign-oriented shortcut using the same original agent flow.", "required": ["prompt"], "optional": ["session_id", "brand_guidelines"]},
    {"name": "analyze_product", "label": "Analyze Product", "description": "Use a product image or product context in a marketing turn.", "required": ["prompt"], "optional": ["image_base64", "session_id"]},
    {"name": "edit_poster", "label": "Edit Poster", "description": "Ask the agent to edit the latest poster HTML in the session.", "required": ["prompt", "session_id"], "optional": []},
    {"name": "list_sessions", "label": "List Sessions", "description": "List recent marketing sessions.", "required": [], "optional": ["limit"]},
    {"name": "get_history", "label": "Get History", "description": "Load messages and generated content for a session.", "required": ["session_id"], "optional": ["limit"]},
    {"name": "get_product_analysis", "label": "Get Product Analysis", "description": "Load cached or persisted product analysis.", "required": ["session_id"], "optional": []},
    {"name": "save_product_analysis", "label": "Save Product Analysis", "description": "Persist product analysis to MongoDB and Redis.", "required": ["session_id", "analysis"], "optional": ["name", "image_id", "image_url"]},
    {"name": "list_providers", "label": "List Providers", "description": "Show configured LLM providers.", "required": [], "optional": []},
    {"name": "switch_provider", "label": "Switch Provider", "description": "Switch active LLM provider at runtime.", "required": ["provider"], "optional": []},
    {"name": "list_capabilities", "label": "List Capabilities", "description": "Show Marketing Agent capabilities.", "required": [], "optional": []},
]

UI_SPEC = {
    "name": AGENT_NAME,
    "description": "Autonomous marketing agent for product analysis, campaign generation, poster HTML, social copy, hashtags, ad copy, and runtime LLM provider switching.",
    "endpoint": "/marketing/action",
    "actions": CAPABILITIES,
    "examples": [
        "Analyze this product image and create Instagram launch copy.",
        "Generate a bold poster and LinkedIn campaign for a productivity app.",
        "Edit the latest poster to make the CTA stronger and the layout cleaner.",
    ],
    "scope": [
        "The copied marketing backend keeps the LangGraph flow, tools, MongoDB sessions, Redis cache, uploads, provider switcher, and content persistence.",
        "The EC2 adapter only normalizes requests and response cards for Pian.",
    ],
    "usage": [
        "Use image attachments from the main app for product/image analysis.",
        "Use provider actions when switching Groq, Gemini, OpenAI, Anthropic, or Ollama.",
        "Use history/product actions for session-level continuity.",
    ],
}

app = FastAPI(title=AGENT_NAME, version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/", response_class=HTMLResponse)
async def root() -> str:
    return render_agent_window(UI_SPEC)


@app.get("/marketing/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "agent": AGENT_ID, "actions": [item["name"] for item in CAPABILITIES]}


@app.post("/marketing/action")
async def action(request: ActionRequest) -> dict[str, Any]:
    payload = _payload(request)
    selected = as_text(payload.get("action") or "run_marketing_agent")
    try:
        if selected in {"run_marketing_agent", "generate_campaign", "analyze_product", "edit_poster", "generate_poster", "chat"}:
            return await _run_marketing(payload, selected)
        if selected == "list_sessions":
            return await _list_sessions(payload, selected)
        if selected in {"get_history", "list_content"}:
            return await _history(payload, selected)
        if selected in {"list_providers", "switch_provider"}:
            return await _providers(payload, selected)
        if selected in {"get_product_analysis", "save_product_analysis"}:
            return await _product(payload, selected)
        if selected == "list_capabilities":
            return success(agent=AGENT_ID, action=selected, summary="Marketing capabilities loaded.", result={"actions": CAPABILITIES}, cards=[card("Capabilities", "Marketing Agent can analyze product media, generate campaigns, manage content sessions, and switch LLM providers.", {"actions": ", ".join(item["name"] for item in CAPABILITIES)})])
        return needs_input(agent=AGENT_ID, action=selected, message=f"Marketing Agent does not expose the action '{selected}'.", missing_fields=["action"])
    except Exception as exc:
        return failed(agent=AGENT_ID, action=selected, public_message="Marketing Agent could not complete this request yet.", error=exc)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8051)
