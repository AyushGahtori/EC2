"""
Chat Routes — /api/chat
Handles both Host and Creator mode text chat + streaming.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel

from app.agents.react_agent import run_agent, run_agent_stream
from app.services.memory_service import (
    append_to_session,
    get_session_history,
    get_session_mode,
    set_session_mode,
)
from app.utils.host_controller import ModeController

router = APIRouter(prefix="/api/chat", tags=["chat"])


# ──────────────────────────────────────────────
# Schemas
# ──────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    mode: Optional[str] = None       # "host" | "creator" | None (use session default)
    stream: bool = False


class ModeRequest(BaseModel):
    session_id: str
    mode: str


class WelcomeRequest(BaseModel):
    session_id: str
    mode: str


# ──────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────

@router.post("/")
async def chat(req: ChatRequest):
    """Main chat endpoint. Returns JSON response."""
    session_id = req.session_id or str(uuid.uuid4())

    # Resolve mode
    if req.mode:
        mode = ModeController.validate_mode(req.mode)
    else:
        # Check if message contains mode switch
        detected = ModeController.detect_mode_from_message(req.message)
        if detected:
            mode = detected
            await set_session_mode(session_id, mode)
        else:
            mode = await get_session_mode(session_id)

    system_prompt = ModeController.get_system_prompt(mode)
    history = await get_session_history(session_id)
    transformed_input = ModeController.transform_input(req.message, mode)

    if req.stream:
        async def event_generator():
            full_response = ""
            async for token in run_agent_stream(
                user_message=transformed_input,
                system_prompt=system_prompt,
                history=history,
                session_id=session_id,
                mode=mode,
            ):
                full_response += token
                yield f"data: {token}\n\n"

            # Save to memory after streaming
            await append_to_session(session_id, "user", req.message)
            await append_to_session(session_id, "assistant", full_response)
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    # Non-streaming
    response = await run_agent(
        user_message=transformed_input,
        system_prompt=system_prompt,
        history=history,
        session_id=session_id,
        mode=mode,
    )

    # Save to memory
    await append_to_session(session_id, "user", req.message)
    await append_to_session(session_id, "assistant", response)

    return {
        "response": response,
        "session_id": session_id,
        "mode": mode,
    }


@router.post("/mode")
async def set_mode(req: ModeRequest):
    """Switch session mode."""
    mode = ModeController.validate_mode(req.mode)
    await set_session_mode(req.session_id, mode)
    welcome = ModeController.get_welcome_message(mode)
    return {"mode": mode, "welcome": welcome, "session_id": req.session_id}


@router.post("/welcome")
async def get_welcome(req: WelcomeRequest):
    """Get welcome message for a mode (used on mode switch)."""
    mode = ModeController.validate_mode(req.mode)
    welcome = ModeController.get_welcome_message(mode)
    # Save welcome to session
    await append_to_session(req.session_id, "assistant", welcome)
    return {"message": welcome, "mode": mode}


@router.get("/history/{session_id}")
async def get_history(session_id: str):
    """Get conversation history for a session."""
    history = await get_session_history(session_id)
    mode = await get_session_mode(session_id)
    return {"session_id": session_id, "mode": mode, "history": history}


@router.delete("/history/{session_id}")
async def clear_history(session_id: str):
    """Clear conversation history."""
    from app.services.memory_service import clear_session
    await clear_session(session_id)
    return {"cleared": True, "session_id": session_id}


@router.get("/sessions")
async def list_sessions():
    """List recent sessions."""
    from app.services.memory_service import list_conversations
    sessions = await list_conversations(limit=20)
    # Remove MongoDB _id for JSON serialization
    for s in sessions:
        s.pop("_id", None)
    return {"sessions": sessions}
