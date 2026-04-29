from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parents[1]
BACKEND_DIR = BASE_DIR / "backend"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

load_dotenv(BASE_DIR / ".env")
load_dotenv(BACKEND_DIR / ".env")

from ec2_shared.api_security import apply_api_security  # noqa: E402
from app.main import create_app  # noqa: E402
from schemas import ShelfieActionRequest, ShelfieActionResponse  # noqa: E402

logger = logging.getLogger("shelfie-grocery-agent")

DISPLAY_NAME = os.getenv("SHELFIE_GROCERY_DISPLAY_NAME", "Shelfie Grocery Agent")

RUN_ACTION_ALIASES = {
    "run_shelfie_grocery_agent",
    "run_shelfie_agent",
    "run",
    "chat",
    "message",
    "ask",
}
HISTORY_ACTION_ALIASES = {"get_history", "chat_history", "history", "list_history"}
SESSION_ACTION_ALIASES = {"list_sessions", "sessions"}
RESET_ACTION_ALIASES = {"reset_session", "new_session", "clear_session"}
CAPABILITY_ACTION_ALIASES = {"list_capabilities", "capabilities", "help", "actions"}


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _normalize_action(action: str) -> str:
    normalized = _clean(action).lower().replace("-", "_").replace(" ", "_")
    if normalized in RUN_ACTION_ALIASES:
        return "run_shelfie_grocery_agent"
    if normalized in HISTORY_ACTION_ALIASES:
        return "get_history"
    if normalized in SESSION_ACTION_ALIASES:
        return "list_sessions"
    if normalized in RESET_ACTION_ALIASES:
        return "reset_session"
    if normalized in CAPABILITY_ACTION_ALIASES:
        return "list_capabilities"
    return normalized


def _resolve_prompt(req: ShelfieActionRequest) -> str:
    return _clean(req.prompt) or _clean(req.message) or _clean(req.query)


def _resolve_session_id(req: ShelfieActionRequest) -> str:
    return (
        _clean(req.session_id)
        or _clean(req.sessionId)
        or _clean(req.chatId)
        or _clean(req.taskId)
        or str(uuid4())
    )


def _resolve_user_id(req: ShelfieActionRequest) -> str:
    return _clean(req.userId) or "anonymous"


def _capabilities_response() -> ShelfieActionResponse:
    return ShelfieActionResponse(
        status="success",
        type="shelfie_capabilities_result",
        message="Shelfie Grocery capabilities loaded.",
        summary="Supports conversation, session history lookup, session listing, and session reset.",
        displayName=DISPLAY_NAME,
        result={
            "actions": [
                "run_shelfie_grocery_agent",
                "get_history",
                "list_sessions",
                "reset_session",
                "list_capabilities",
            ],
            "requiredFields": {
                "run_shelfie_grocery_agent": ["prompt/message/query"],
                "get_history": ["session_id or chatId"],
                "list_sessions": [],
                "reset_session": ["session_id or chatId"],
            },
            "optionalFields": {
                "run_shelfie_grocery_agent": ["session_id", "userId"],
                "get_history": [],
                "list_sessions": ["limit", "userId"],
                "reset_session": [],
            },
            "providers": ["ollama", "ollama_cloud", "openai", "anthropic", "gemini"],
            "stores": {
                "session_cache": "redis_with_memory_fallback",
                "history_store": "mongodb_with_file_fallback",
            },
        },
    )


app: FastAPI = create_app()
apply_api_security(app)


@app.get("/health")
def health() -> dict[str, str]:
    settings = app.state.settings
    return {
        "status": "healthy",
        "agent": "shelfie-grocery-agent",
        "displayName": DISPLAY_NAME,
        "provider": settings.LLM_PROVIDER,
        "model": settings.active_model,
        "version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/shelfie/health")
def shelfie_health() -> dict[str, str]:
    return health()


@app.post("/shelfie/action", response_model=ShelfieActionResponse)
@app.post("/shelfie-grocery/action", response_model=ShelfieActionResponse)
async def shelfie_action(req: ShelfieActionRequest) -> ShelfieActionResponse:
    service = app.state.agent_service
    settings = app.state.settings
    action = _normalize_action(req.action)

    try:
        if action == "list_capabilities":
            return _capabilities_response()

        if action == "run_shelfie_grocery_agent":
            prompt = _resolve_prompt(req)
            if not prompt:
                return ShelfieActionResponse(
                    status="needs_input",
                    type="shelfie_conversation_result",
                    message="Please share what you want to plan or ask.",
                    summary="Prompt is required before Shelfie can respond.",
                    error="missing_prompt",
                    displayName=DISPLAY_NAME,
                    result={"suggestedInputs": ["prompt/message/query"]},
                )

            session_id = _resolve_session_id(req)
            user_id = _resolve_user_id(req)
            reply = await service.generate_reply(
                session_id=session_id,
                user_id=user_id,
                user_message=prompt,
            )
            history = await service.get_history(session_id)
            return ShelfieActionResponse(
                status="success",
                type="shelfie_conversation_result",
                message="Shelfie response generated.",
                summary=reply,
                displayName=DISPLAY_NAME,
                result={
                    "session_id": session_id,
                    "user_id": user_id,
                    "provider": settings.LLM_PROVIDER,
                    "model": settings.active_model,
                    "responseText": reply,
                    "history": history,
                },
            )

        if action == "get_history":
            session_id = _clean(req.session_id) or _clean(req.sessionId) or _clean(req.chatId)
            if not session_id:
                return ShelfieActionResponse(
                    status="needs_input",
                    type="shelfie_history_result",
                    message="Please provide a session identifier to load history.",
                    summary="History lookup requires session_id, sessionId, or chatId.",
                    error="missing_session_id",
                    displayName=DISPLAY_NAME,
                    result={"suggestedInputs": ["session_id or chatId"]},
                )

            history = await service.get_history(session_id)
            return ShelfieActionResponse(
                status="success",
                type="shelfie_history_result",
                message="Session history loaded.",
                summary=f"Loaded {len(history)} message(s) from this session.",
                displayName=DISPLAY_NAME,
                result={"session_id": session_id, "history": history},
            )

        if action == "list_sessions":
            user_id = _resolve_user_id(req)
            limit = max(1, min(int(req.limit or 20), 100))
            sessions = await service.list_sessions(user_id=user_id, limit=limit)
            return ShelfieActionResponse(
                status="success",
                type="shelfie_sessions_result",
                message="Recent sessions loaded.",
                summary=f"Loaded {len(sessions)} session(s).",
                displayName=DISPLAY_NAME,
                result={"user_id": user_id, "sessions": sessions},
            )

        if action == "reset_session":
            session_id = _clean(req.session_id) or _clean(req.sessionId) or _clean(req.chatId)
            if not session_id:
                return ShelfieActionResponse(
                    status="needs_input",
                    type="shelfie_reset_result",
                    message="Please provide the session to reset.",
                    summary="Session reset requires session_id, sessionId, or chatId.",
                    error="missing_session_id",
                    displayName=DISPLAY_NAME,
                    result={"suggestedInputs": ["session_id or chatId"]},
                )

            await service.reset_session(session_id)
            return ShelfieActionResponse(
                status="success",
                type="shelfie_reset_result",
                message="Session reset complete.",
                summary="Session memory and stored history were cleared for this session.",
                displayName=DISPLAY_NAME,
                result={"session_id": session_id},
            )

        return ShelfieActionResponse(
            status="failed",
            type="shelfie_grocery_result",
            message=f"Unsupported action: {req.action}",
            summary="Use run_shelfie_grocery_agent, get_history, list_sessions, reset_session, or list_capabilities.",
            error=f"unknown_action:{req.action}",
            displayName=DISPLAY_NAME,
            result={"supportedActions": _capabilities_response().result["actions"]},
        )
    except Exception as exc:
        logger.exception("Shelfie action failed: %s", exc)
        return ShelfieActionResponse(
            status="failed",
            type="shelfie_grocery_result",
            message="Shelfie Grocery Agent failed to process this request.",
            summary="Please retry. If this continues, run list_capabilities and verify required fields.",
            error=f"shelfie_grocery_failed:{exc.__class__.__name__}",
            displayName=DISPLAY_NAME,
        )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8045"))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
