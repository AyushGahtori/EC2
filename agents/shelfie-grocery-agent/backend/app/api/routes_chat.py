from uuid import uuid4
import logging

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas.chat import (
    ChatHistoryResponse,
    ChatRequest,
    ChatResponse,
    ChatSessionsResponse,
)


router = APIRouter(tags=["chat"])
logger = logging.getLogger("shelfie-grocery-agent.routes-chat")


@router.post("/chat/messages", response_model=ChatResponse)
async def chat(request: Request, payload: ChatRequest) -> ChatResponse:
    service = request.app.state.agent_service
    settings = request.app.state.settings
    session_id = payload.session_id or str(uuid4())

    try:
        assistant_message = await service.generate_reply(
            session_id=session_id,
            user_id=payload.user_id,
            user_message=payload.message,
        )
    except Exception as exc:
        logger.exception("Chat generation failed for session %s", session_id)
        raise HTTPException(status_code=500, detail="Chat generation failed.") from exc

    return ChatResponse(
        session_id=session_id,
        provider=settings.LLM_PROVIDER,
        model=settings.active_model,
        message=assistant_message,
    )


@router.get("/chat/history/{session_id}", response_model=ChatHistoryResponse)
async def chat_history(request: Request, session_id: str) -> ChatHistoryResponse:
    service = request.app.state.agent_service
    try:
        messages = await service.get_history(session_id)
    except Exception as exc:
        logger.exception("Chat history load failed for session %s", session_id)
        raise HTTPException(status_code=500, detail="Failed to load chat history.") from exc

    return ChatHistoryResponse(session_id=session_id, messages=messages)


@router.get("/chat/sessions", response_model=ChatSessionsResponse)
async def chat_sessions(
    request: Request,
    user_id: str = Query(..., min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> ChatSessionsResponse:
    service = request.app.state.agent_service
    try:
        sessions = await service.list_sessions(user_id=user_id, limit=limit)
    except Exception as exc:
        logger.exception("Chat sessions list failed for user %s", user_id)
        raise HTTPException(status_code=500, detail="Failed to list sessions.") from exc

    return ChatSessionsResponse(user_id=user_id, sessions=sessions)
