"""
Chat API routes — handles message sending and SSE streaming.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from datetime import datetime
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from agents.marketing_agent import run_agent_streaming
from models.schemas import ChatRequest, MessageDocument, MessageRole
from services.mongodb_service import (
    get_latest_content,
    get_messages,
    get_product,
    get_session,
    save_message,
    update_session,
)
from services.redis_service import get_cached_product_analysis, get_session_context

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


async def _load_product_image_b64(image_id: Optional[str]) -> Optional[str]:
    """Load a previously uploaded image as base64."""
    if not image_id:
        return None
    from config.settings import get_settings
    import aiofiles
    from pathlib import Path

    settings = get_settings()
    img_path = Path(settings.upload_dir) / image_id

    if img_path.exists():
        async with aiofiles.open(img_path, "rb") as f:
            data = await f.read()
        return base64.b64encode(data).decode("utf-8")
    return None


async def _event_generator(
    session_id: str,
    user_message: str,
    message_history: list[dict],
    product_image_b64: Optional[str],
    product_image_url: Optional[str],
    product_context: Optional[str],
    latest_poster_html: Optional[str],
    full_response_parts: list,
) -> AsyncGenerator[dict, None]:
    """
    Wraps the agent stream and collects the final response for persistence.
    """
    async for event in run_agent_streaming(
        session_id=session_id,
        user_message=user_message,
        message_history=message_history,
        product_image_b64=product_image_b64,
        product_image_url=product_image_url,
        product_context=product_context,
        latest_poster_html=latest_poster_html,
    ):
        # Collect tokens to build full assistant response
        if event.get("type") == "token":
            full_response_parts.append(event.get("content", ""))

        yield {"data": json.dumps(event)}

        # Slight yield to prevent blocking
        if event.get("type") == "token":
            await asyncio.sleep(0)


@router.api_route("/stream/{session_id}", methods=["GET", "POST"])
async def chat_stream(
    session_id: str,
    request: Request,
    message: str = Query(..., description="User message"),
    image_id: Optional[str] = Query(None, description="Uploaded image ID"),
):
    """
    SSE streaming endpoint for chat.
    EventSource clients use GET, but POST is also accepted for compatibility.
    """
    # Verify session exists
    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Load message history from MongoDB
    raw_messages = await get_messages(session_id, limit=20)
    history = [{"role": m["role"], "content": m["content"]} for m in raw_messages]

    # Load product image if provided. Build an ABSOLUTE URL so the poster
    # iframe (served from the frontend origin) can still fetch the image
    # directly from the backend.
    product_image_b64 = await _load_product_image_b64(image_id)
    if image_id:
        base = str(request.base_url).rstrip("/")
        product_image_url = f"{base}/uploads/{image_id}"
    else:
        product_image_url = session.get("product_image_url")

    # Fallback: if this turn has no newly-uploaded image but the session
    # remembers one, load that file from disk so the agent can still
    # pre-analyse it on the first ask rather than fumbling with the URL.
    if not product_image_b64 and product_image_url:
        from urllib.parse import urlparse
        parsed_path = urlparse(product_image_url).path or ""
        marker = "/uploads/"
        if marker in parsed_path:
            fallback_image_id = parsed_path.rsplit(marker, 1)[-1]
            if fallback_image_id:
                product_image_b64 = await _load_product_image_b64(fallback_image_id)

    # Load product context from cache/DB
    product_context = await get_cached_product_analysis(session_id)
    if not product_context:
        product = await get_product(session_id)
        if product:
            product_context = product.get("analysis")

    # Load brand guidelines from session
    brand_guidelines = session.get("brand_guidelines")

    # Load the most recent poster HTML so the agent can edit it on request
    latest_poster_doc = await get_latest_content(session_id, "poster")
    latest_poster_html = latest_poster_doc.get("content") if latest_poster_doc else None

    # Save user message to DB
    await save_message(MessageDocument(
        session_id=session_id,
        role=MessageRole.USER,
        content=message,
        image_id=image_id,
    ))

    # Collect response tokens to save assistant message afterward
    full_response_parts: list[str] = []

    async def event_stream():
        async for event in _event_generator(
            session_id=session_id,
            user_message=message,
            message_history=history,
            product_image_b64=product_image_b64,
            product_image_url=product_image_url,
            product_context=product_context,
            latest_poster_html=latest_poster_html,
            full_response_parts=full_response_parts,
        ):
            yield event

        # After streaming is done, save the assistant response
        if full_response_parts:
            full_response = "".join(full_response_parts)
            await save_message(MessageDocument(
                session_id=session_id,
                role=MessageRole.ASSISTANT,
                content=full_response,
            ))
            await update_session(session_id, {"updated_at": datetime.utcnow()})

    return EventSourceResponse(event_stream())


@router.post("")
async def chat(body: ChatRequest):
    """
    Non-streaming chat endpoint. Returns full response at once.
    Useful for testing or clients that don't support SSE.
    """
    session = await get_session(body.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    raw_messages = await get_messages(body.session_id, limit=20)
    history = [{"role": m["role"], "content": m["content"]} for m in raw_messages]

    product_image_b64 = await _load_product_image_b64(body.image_id)
    product_context = await get_cached_product_analysis(body.session_id)

    await save_message(MessageDocument(
        session_id=body.session_id,
        role=MessageRole.USER,
        content=body.message,
        image_id=body.image_id,
    ))

    from agents.marketing_agent import get_final_response
    response = await get_final_response(
        session_id=body.session_id,
        user_message=body.message,
        message_history=history,
        product_image_b64=product_image_b64,
        product_context=product_context,
    )

    await save_message(MessageDocument(
        session_id=body.session_id,
        role=MessageRole.ASSISTANT,
        content=response,
    ))

    return {"response": response, "session_id": body.session_id}


@router.get("/history/{session_id}")
async def get_chat_history(session_id: str, limit: int = Query(50, ge=1, le=200)):
    """Get chat history for a session."""
    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = await get_messages(session_id, limit=limit)
    return {"messages": messages, "session_id": session_id}
