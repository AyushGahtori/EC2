"""
Session management routes — create, list, delete sessions.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from models.schemas import SessionCreateRequest, SessionDocument, SessionResponse
from services.mongodb_service import (
    create_session,
    delete_session,
    get_content,
    get_session,
    list_sessions,
    update_session,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=SessionResponse)
async def create_new_session(body: SessionCreateRequest):
    """Create a new chat session."""
    session_id = str(uuid.uuid4())
    now = datetime.utcnow()

    session = SessionDocument(
        session_id=session_id,
        name=body.name or f"Session {now.strftime('%b %d, %H:%M')}",
        product_name=body.product_name,
        created_at=now,
        updated_at=now,
    )

    await create_session(session)

    return SessionResponse(
        session_id=session_id,
        name=session.name,
        product_name=session.product_name,
        created_at=now,
        message_count=0,
    )


@router.get("", response_model=list[SessionResponse])
async def list_all_sessions(limit: int = Query(50, ge=1, le=200)):
    """List all sessions, most recently updated first."""
    sessions = await list_sessions(limit=limit)
    return [
        SessionResponse(
            session_id=s["session_id"],
            name=s.get("name", "Unnamed Session"),
            product_name=s.get("product_name"),
            created_at=s.get("created_at", datetime.utcnow()),
            message_count=s.get("message_count", 0),
        )
        for s in sessions
    ]


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session_detail(session_id: str):
    """Get session details."""
    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return SessionResponse(
        session_id=session["session_id"],
        name=session.get("name", "Unnamed Session"),
        product_name=session.get("product_name"),
        created_at=session.get("created_at", datetime.utcnow()),
        message_count=session.get("message_count", 0),
    )


@router.patch("/{session_id}")
async def update_session_details(session_id: str, body: SessionCreateRequest):
    """Update session name or product name."""
    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    updates = {}
    if body.name:
        updates["name"] = body.name
    if body.product_name:
        updates["product_name"] = body.product_name
    if body.brand_guidelines is not None:
        updates["brand_guidelines"] = body.brand_guidelines

    if updates:
        await update_session(session_id, updates)

    return {"success": True}


@router.delete("/{session_id}")
async def delete_session_route(session_id: str):
    """Delete a session and all associated data."""
    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    await delete_session(session_id)
    return {"success": True, "message": "Session deleted"}


@router.get("/{session_id}/content")
async def get_session_content(session_id: str, limit: int = Query(20, ge=1, le=100)):
    """Get all generated content for a session."""
    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    content = await get_content(session_id, limit=limit)
    return {"content": content, "session_id": session_id}
