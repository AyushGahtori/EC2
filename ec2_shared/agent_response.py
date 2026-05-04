"""Response contract helpers for EC2 agent adapters.

The Next app expects a compact execution contract. These helpers keep every
adapter consistent and keep internal exception details out of user-facing text.
"""
from __future__ import annotations

import traceback
from typing import Any, Iterable


def _clean_list(values: Iterable[Any] | None) -> list[str]:
    if not values:
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def card(title: str, body: str | None = None, fields: dict[str, Any] | None = None, tone: str = "neutral") -> dict[str, Any]:
    return {
        "title": title,
        "body": body or "",
        "fields": fields or {},
        "tone": tone,
    }


def success(
    *,
    agent: str,
    action: str,
    summary: str,
    result: dict[str, Any] | None = None,
    cards: list[dict[str, Any]] | None = None,
    logs: list[str] | None = None,
    next_actions: list[str] | None = None,
    internal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = result or {}
    return {
        "status": "success",
        "summary": summary,
        "error": None,
        "agent": agent,
        "action": action,
        "result": payload,
        "recommended_next_actions": _clean_list(next_actions),
        "ui_payload": {
            "kind": "agent_result",
            "agent": agent,
            "action": action,
            "summary": summary,
            "cards": cards or [card("Result", summary, payload)],
            "logs": _clean_list(logs),
        },
        "internal_payload": internal or {},
    }


def partial_success(
    *,
    agent: str,
    action: str,
    summary: str,
    result: dict[str, Any] | None = None,
    cards: list[dict[str, Any]] | None = None,
    logs: list[str] | None = None,
    next_actions: list[str] | None = None,
    internal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = success(
        agent=agent,
        action=action,
        summary=summary,
        result=result,
        cards=cards,
        logs=logs,
        next_actions=next_actions,
        internal=internal,
    )
    response["status"] = "partial_success"
    return response


def needs_input(
    *,
    agent: str,
    action: str,
    message: str,
    missing_fields: list[str],
    next_actions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": "needs_input",
        "summary": message,
        "message": message,
        "error": None,
        "agent": agent,
        "action": action,
        "result": {"missing_fields": missing_fields},
        "recommended_next_actions": _clean_list(next_actions)
        or [f"Provide: {', '.join(missing_fields)}"],
        "ui_payload": {
            "kind": "needs_input",
            "agent": agent,
            "action": action,
            "summary": message,
            "suggestedInputs": missing_fields,
            "cards": [
                card(
                    "More details needed",
                    message,
                    {"missing_fields": ", ".join(missing_fields)},
                    "warning",
                )
            ],
            "logs": [],
        },
        "internal_payload": {"missing_fields": missing_fields},
    }


def failed(
    *,
    agent: str,
    action: str,
    public_message: str,
    error: Exception | str,
    code: str = "AGENT_EXECUTION_FAILED",
) -> dict[str, Any]:
    raw_error = str(error)
    trace = traceback.format_exc() if not isinstance(error, str) else ""
    return {
        "status": "failed",
        "summary": public_message,
        "error": public_message,
        "error_code": code,
        "error_context": {
            "agent": agent,
            "action": action,
            "raw_error": raw_error,
        },
        "agent": agent,
        "action": action,
        "result": {},
        "recommended_next_actions": [
            "Check the agent service configuration and retry.",
            "Provide any missing required fields if the request was incomplete.",
        ],
        "ui_payload": {
            "kind": "agent_error",
            "agent": agent,
            "action": action,
            "summary": public_message,
            "cards": [card("Agent could not finish", public_message, {}, "danger")],
            "logs": ["The adapter captured the failure without exposing raw internals to chat."],
        },
        "internal_payload": {
            "raw_error": raw_error,
            "traceback": trace,
        },
    }


def require_fields(agent: str, action: str, payload: dict[str, Any], fields: list[str]) -> dict[str, Any] | None:
    missing = [
        field
        for field in fields
        if payload.get(field) is None or (isinstance(payload.get(field), str) and not payload.get(field, "").strip())
    ]
    if not missing:
        return None
    return needs_input(
        agent=agent,
        action=action,
        message=f"{action.replace('_', ' ').title()} needs {', '.join(missing)} before it can run.",
        missing_fields=missing,
    )


def as_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    if isinstance(value, str):
        return value.strip() or fallback
    return str(value)
