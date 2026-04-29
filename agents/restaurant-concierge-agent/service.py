from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from config import Config
from main import RestaurantAIAgent
from models.shared_memory import SharedMemory
from schemas import RestaurantConciergeActionRequest, RestaurantConciergeActionResponse
from store import list_session_logs, load_session, reset_session, save_interaction_log, save_session, validated_uid


DISPLAY_NAME = Config.DISPLAY_NAME
SUPPORTED_ACTIONS = {
    "run_restaurant_concierge",
    "browse_menu",
    "search_menu",
    "get_item_details",
    "get_recommendations",
    "get_order_summary",
    "get_session_analytics",
    "reset_session",
    "suggest_items",
    "request_human_help",
    "list_capabilities",
}

ACTION_ALIASES = {
    "run": "run_restaurant_concierge",
    "chat": "run_restaurant_concierge",
    "ask": "run_restaurant_concierge",
    "order": "run_restaurant_concierge",
    "menu": "browse_menu",
    "show_menu": "browse_menu",
    "search": "search_menu",
    "item_details": "get_item_details",
    "details": "get_item_details",
    "recommend": "get_recommendations",
    "recommendations": "get_recommendations",
    "summary": "get_order_summary",
    "analytics": "get_session_analytics",
    "reset": "reset_session",
    "suggest": "suggest_items",
    "handoff": "request_human_help",
    "help": "list_capabilities",
    "capabilities": "list_capabilities",
}


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _normalize_action(action: str) -> str:
    normalized = _clean(action).lower().replace("-", "_").replace(" ", "_")
    return ACTION_ALIASES.get(normalized, normalized)


def _primary_text(req: RestaurantConciergeActionRequest) -> str:
    for value in (req.prompt, req.message, req.query, req.itemName, req.reason):
        cleaned = _clean(value)
        if cleaned:
            return cleaned
    return ""


def _short_summary(text: str, max_chars: int = 180) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    if len(flat) <= max_chars:
        return flat
    return f"{flat[: max_chars - 3]}..."


def _session_key(req: RestaurantConciergeActionRequest, uid: str | None) -> str:
    raw_seed = _clean(req.sessionId) or _clean(req.chatId) or _clean(req.taskId) or _clean(req.agentId) or "default"
    owner = uid or "anonymous"
    digest = hashlib.sha256(f"{owner}:{raw_seed}".encode("utf-8")).hexdigest()[:16]
    slug_source = re.sub(r"[^A-Za-z0-9_-]+", "-", raw_seed).strip("-").lower() or "session"
    return f"{slug_source[:40]}-{digest}"


def _session_meta(req: RestaurantConciergeActionRequest, uid: str | None, session_key: str, source: str | None) -> dict[str, Any]:
    return {
        "userId": uid,
        "chatId": _clean(req.chatId) or None,
        "taskId": _clean(req.taskId) or None,
        "sessionId": session_key,
        "source": source or "new",
        "ttlSeconds": Config.SESSION_TTL_SECONDS,
    }


def _build_agent(req: RestaurantConciergeActionRequest, uid: str | None, session_key: str) -> tuple[RestaurantAIAgent, str | None]:
    restored_payload = None
    restored_source = None
    if not req.forceReset:
        restored_payload, restored_source = load_session(uid, session_key, Config.SESSION_TTL_SECONDS)
    shared_memory = SharedMemory.from_dict(restored_payload)
    agent = RestaurantAIAgent(
        session_id=session_key,
        shared_memory=shared_memory,
        enable_console=False,
    )
    return agent, restored_source


def _capabilities_response() -> RestaurantConciergeActionResponse:
    return RestaurantConciergeActionResponse(
        status="success",
        type="restaurant_capabilities",
        displayName=DISPLAY_NAME,
        message="Restaurant Concierge capabilities loaded.",
        summary="Supports menu browsing, ordering, order review, suggestions, analytics, and human handoff.",
        result={
            "actions": sorted(SUPPORTED_ACTIONS),
            "actionAliases": ACTION_ALIASES,
            "requiredFields": {
                "run_restaurant_concierge": ["prompt or message"],
                "search_menu": ["query"],
                "get_item_details": ["itemName"],
                "suggest_items": ["prompt or query"],
                "request_human_help": ["reason"],
            },
            "optionalFields": {
                "browse_menu": ["category", "dietaryFilter"],
                "run_restaurant_concierge": ["chatId", "sessionId", "forceReset"],
                "get_session_analytics": ["chatId", "sessionId"],
            },
            "examples": [
                "Show me the vegetarian menu",
                "Add 2 biryanis and 1 chai",
                "Remove the chai and make it pickup",
                "What do you recommend with butter chicken?",
            ],
        },
    )


def _response_payload(
    req: RestaurantConciergeActionRequest,
    agent: RestaurantAIAgent,
    session_key: str,
    session_source: str | None,
    *,
    action: str,
    response_text: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    order = agent.get_order_details()
    analytics = agent.get_conversation_analytics()
    state = agent.coordinator.shared_memory.to_dict()
    logs = list_session_logs(validated_uid(req.userId), session_key, limit=8)
    payload = {
        "action": action,
        "responseText": response_text,
        "session": _session_meta(req, validated_uid(req.userId), session_key, session_source),
        "state": state,
        "order": order,
        "analytics": analytics,
        "logs": logs,
        "controls": {
            "primary": ["browse_menu", "get_recommendations", "get_order_summary", "reset_session"],
            "secondary": ["search_menu", "suggest_items", "request_human_help", "get_session_analytics"],
        },
    }
    if extra:
        payload.update(extra)
    return payload


def _needs_input(message: str, suggested_inputs: list[str], *, result_type: str, error: str) -> RestaurantConciergeActionResponse:
    return RestaurantConciergeActionResponse(
        status="needs_input",
        type=result_type,
        displayName=DISPLAY_NAME,
        message=message,
        summary="Please provide the missing input and try again.",
        result={"suggestedInputs": suggested_inputs},
        error=error,
    )


async def run_restaurant_concierge_action(
    req: RestaurantConciergeActionRequest,
) -> RestaurantConciergeActionResponse:
    try:
        uid = validated_uid(req.userId)
        action = _normalize_action(req.action)
        if action not in SUPPORTED_ACTIONS:
            return RestaurantConciergeActionResponse(
                status="failed",
                type="restaurant_error",
                displayName=DISPLAY_NAME,
                message=f"Unsupported action: {req.action}",
                summary="Use list_capabilities to view supported restaurant actions.",
                result={"supportedActions": sorted(SUPPORTED_ACTIONS)},
                error=f"unknown_action:{req.action}",
            )

        if action == "list_capabilities":
            return _capabilities_response()

        session_key = _session_key(req, uid)
        agent, session_source = _build_agent(req, uid, session_key)

        if action == "reset_session":
            reset_session(uid, session_key)
            agent.reset_conversation(new_session_id=session_key)
            payload = _response_payload(
                req,
                agent,
                session_key,
                "reset",
                action=action,
                response_text="Session reset. You can start a fresh restaurant conversation now.",
                extra={"sessionReset": True},
            )
            save_session(uid, session_key, agent.coordinator.shared_memory.to_dict())
            response = RestaurantConciergeActionResponse(
                status="success",
                type="restaurant_reset_result",
                displayName=DISPLAY_NAME,
                message="Session reset.",
                summary="Restaurant session was cleared and is ready for a new request.",
                result=payload,
            )
            save_interaction_log(uid, session_key, action=action, status=response.status, payload=response.model_dump(mode="json"))
            return response

        if action == "browse_menu":
            response_text = agent.get_menu(category=_clean(req.category) or None, dietary_filter=_clean(req.dietaryFilter) or None)
            payload = _response_payload(
                req,
                agent,
                session_key,
                session_source,
                action=action,
                response_text=response_text,
                extra={"menu": {"category": _clean(req.category) or None, "dietaryFilter": _clean(req.dietaryFilter) or None}},
            )
            response = RestaurantConciergeActionResponse(
                status="success",
                type="restaurant_menu_result",
                displayName=DISPLAY_NAME,
                message="Menu ready.",
                summary="Loaded the restaurant menu view.",
                result=payload,
            )
        elif action == "search_menu":
            query = _clean(req.query) or _clean(req.prompt) or _clean(req.message)
            if not query:
                return _needs_input(
                    "I need a search query to look through the menu.",
                    ["query"],
                    result_type="restaurant_menu_result",
                    error="missing_query",
                )
            response_text = agent.search_menu(query)
            payload = _response_payload(
                req,
                agent,
                session_key,
                session_source,
                action=action,
                response_text=response_text,
                extra={"query": query},
            )
            response = RestaurantConciergeActionResponse(
                status="success",
                type="restaurant_menu_result",
                displayName=DISPLAY_NAME,
                message="Menu search completed.",
                summary=f"Searched the menu for '{query}'.",
                result=payload,
            )
        elif action == "get_item_details":
            item_name = _clean(req.itemName) or _clean(req.prompt) or _clean(req.message)
            if not item_name:
                return _needs_input(
                    "I need the item name before I can show its details.",
                    ["itemName"],
                    result_type="restaurant_item_result",
                    error="missing_item_name",
                )
            item = agent.get_menu_item_details(item_name)
            if not item:
                return RestaurantConciergeActionResponse(
                    status="failed",
                    type="restaurant_item_result",
                    displayName=DISPLAY_NAME,
                    message=f"I could not find '{item_name}' on the menu.",
                    summary="Try searching the menu or use a more specific item name.",
                    result={"itemName": item_name},
                    error="item_not_found",
                )
            response_text = (
                f"{item['name']} costs INR {float(item.get('price', 0.0)):.2f}. "
                f"{item.get('description', '')}"
            )
            payload = _response_payload(
                req,
                agent,
                session_key,
                session_source,
                action=action,
                response_text=response_text,
                extra={"item": item},
            )
            response = RestaurantConciergeActionResponse(
                status="success",
                type="restaurant_item_result",
                displayName=DISPLAY_NAME,
                message=f"Loaded details for {item['name']}.",
                summary=f"{item['name']} is ready to review.",
                result=payload,
            )
        elif action == "get_recommendations":
            response_text = agent.get_recommendations()
            payload = _response_payload(req, agent, session_key, session_source, action=action, response_text=response_text)
            response = RestaurantConciergeActionResponse(
                status="success",
                type="restaurant_recommendation_result",
                displayName=DISPLAY_NAME,
                message="Recommendations ready.",
                summary="Loaded chef recommendations and popular menu items.",
                result=payload,
            )
        elif action == "get_order_summary":
            order = agent.get_order_details()
            response_text = "Your current order is ready." if order.get("items") else "There is no active order yet."
            payload = _response_payload(req, agent, session_key, session_source, action=action, response_text=response_text)
            response = RestaurantConciergeActionResponse(
                status="success",
                type="restaurant_order_result",
                displayName=DISPLAY_NAME,
                message="Order summary ready.",
                summary="Loaded the current restaurant order state.",
                result=payload,
            )
        elif action == "get_session_analytics":
            payload = _response_payload(req, agent, session_key, session_source, action=action, response_text="Session analytics ready.")
            response = RestaurantConciergeActionResponse(
                status="success",
                type="restaurant_analytics_result",
                displayName=DISPLAY_NAME,
                message="Session analytics ready.",
                summary="Loaded conversation analytics, state, and recent logs.",
                result=payload,
            )
        elif action == "suggest_items":
            text = _clean(req.prompt) or _clean(req.query) or _clean(req.message)
            if not text:
                return _needs_input(
                    "I need partial input before I can suggest likely menu items.",
                    ["prompt or query"],
                    result_type="restaurant_suggestion_result",
                    error="missing_partial_input",
                )
            response_text = agent.get_intelligent_suggestions(text)
            payload = _response_payload(
                req,
                agent,
                session_key,
                session_source,
                action=action,
                response_text=response_text,
                extra={"partialInput": text},
            )
            response = RestaurantConciergeActionResponse(
                status="success",
                type="restaurant_suggestion_result",
                displayName=DISPLAY_NAME,
                message="Suggestions ready.",
                summary="Generated likely menu suggestions from partial input.",
                result=payload,
            )
        elif action == "request_human_help":
            reason = _clean(req.reason) or _clean(req.prompt) or _clean(req.message)
            if not reason:
                return _needs_input(
                    "I need a reason before escalating this to a human teammate.",
                    ["reason"],
                    result_type="restaurant_handoff_result",
                    error="missing_reason",
                )
            agent.simulate_human_intervention(reason)
            payload = _response_payload(
                req,
                agent,
                session_key,
                session_source,
                action=action,
                response_text="A human teammate has been requested for this restaurant session.",
                extra={"handoffReason": reason},
            )
            response = RestaurantConciergeActionResponse(
                status="action_required",
                type="restaurant_handoff_result",
                displayName=DISPLAY_NAME,
                message="Human assistance requested.",
                summary="The session has been flagged for human follow-up.",
                result=payload,
            )
        else:
            prompt = _primary_text(req)
            if not prompt:
                return _needs_input(
                    "I need your restaurant request before I can continue.",
                    ["prompt or message"],
                    result_type="restaurant_conversation_result",
                    error="missing_prompt",
                )
            response_text = agent.process_single_request(prompt)
            payload = _response_payload(req, agent, session_key, session_source, action=action, response_text=response_text)
            response = RestaurantConciergeActionResponse(
                status="action_required" if agent.coordinator.shared_memory.needs_human_intervention else "success",
                type="restaurant_conversation_result",
                displayName=DISPLAY_NAME,
                message=response_text,
                summary=_short_summary(response_text),
                result=payload,
                error="human_intervention_requested" if agent.coordinator.shared_memory.needs_human_intervention else None,
            )

        save_session(uid, session_key, agent.coordinator.shared_memory.to_dict())
        save_interaction_log(uid, session_key, action=action, status=response.status, payload=response.model_dump(mode="json"))
        return response
    except ValueError as exc:
        return RestaurantConciergeActionResponse(
            status="needs_input",
            type="restaurant_error",
            displayName=DISPLAY_NAME,
            message=str(exc),
            summary="Required request fields are missing or invalid.",
            result={"suggestedInputs": ["userId"]},
            error=str(exc),
        )
    except Exception as exc:
        return RestaurantConciergeActionResponse(
            status="failed",
            type="restaurant_error",
            displayName=DISPLAY_NAME,
            message="Restaurant Concierge failed to process this request.",
            summary="Retry with a clearer request. If it keeps failing, use the reset action and try again.",
            error=f"restaurant_concierge_failed:{exc.__class__.__name__}",
        )
