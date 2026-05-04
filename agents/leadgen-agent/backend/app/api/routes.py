"""
FastAPI routes for the lead generation agent.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agent.graph import run_agent
from app.services.mongodb_client import get_leads, count_leads
from app.services.redis_client import (
    conversation_append,
    conversation_clear,
    conversation_get,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────────────────────────
# Request / Response Models
# ─────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    session_id: str
    message: str


class LeadsResponse(BaseModel):
    leads: list[dict]
    total: int
    session_id: Optional[str]


# ─────────────────────────────────────────────
# Chat (streaming)
# ─────────────────────────────────────────────

@router.post("/chat/stream")
async def chat_stream(body: ChatRequest):
    """
    Stream the agent's response using Server-Sent Events (SSE).
    The agent autonomously decides what to do based on the message.
    """
    session_id = body.session_id or str(uuid.uuid4())
    user_message = body.message.strip()

    if not user_message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # Save user message to Redis
    await conversation_append(session_id, "user", user_message)

    # Get conversation history
    history = await conversation_get(session_id)

    async def generate():
        full_response = ""
        try:
            async for chunk in run_agent(
                query=user_message,
                session_id=session_id,
                conversation_history=history[:-1],  # exclude the message we just added
            ):
                full_response += chunk
                # SSE format
                data = json.dumps({"type": "chunk", "content": chunk, "session_id": session_id})
                yield f"data: {data}\n\n"

            # Save assistant response
            await conversation_append(session_id, "assistant", full_response)

            # Signal completion
            yield f"data: {json.dumps({'type': 'done', 'session_id': session_id})}\n\n"

        except Exception as e:
            logger.error(f"Stream error: {e}")
            error_data = json.dumps({"type": "error", "content": str(e)})
            yield f"data: {error_data}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Session-ID": session_id,
        },
    )


@router.post("/chat")
async def chat(body: ChatRequest):
    """Non-streaming chat endpoint (for testing)."""
    session_id = body.session_id or str(uuid.uuid4())
    user_message = body.message.strip()

    if not user_message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    await conversation_append(session_id, "user", user_message)
    history = await conversation_get(session_id)

    full_response = ""
    async for chunk in run_agent(
        query=user_message,
        session_id=session_id,
        conversation_history=history[:-1],
    ):
        full_response += chunk

    await conversation_append(session_id, "assistant", full_response)

    return ChatResponse(session_id=session_id, message=full_response)


# ─────────────────────────────────────────────
# Leads
# ─────────────────────────────────────────────

@router.get("/leads")
async def list_leads(
    session_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    skip: int = Query(0, ge=0),
):
    """Retrieve stored leads, optionally filtered by session."""
    leads = await get_leads(session_id=session_id, limit=limit, skip=skip)
    total = await count_leads(session_id=session_id)
    return LeadsResponse(leads=leads, total=total, session_id=session_id)


@router.delete("/leads/session/{session_id}")
async def clear_session_leads(session_id: str):
    """Clear conversation history for a session."""
    await conversation_clear(session_id)
    return {"message": f"Session {session_id} cleared"}


# ─────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────

@router.get("/health")
async def health():
    return {"status": "ok", "service": "leadgen-agent"}


@router.get("/session/new")
async def new_session():
    """Generate a fresh session ID."""
    return {"session_id": str(uuid.uuid4())}
