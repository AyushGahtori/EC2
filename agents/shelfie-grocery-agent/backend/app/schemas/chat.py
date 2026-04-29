from datetime import datetime

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8_000)
    session_id: str | None = None
    user_id: str = "anonymous"


class ChatResponse(BaseModel):
    session_id: str
    provider: str
    model: str
    message: str


class ChatHistoryResponse(BaseModel):
    session_id: str
    messages: list[dict[str, str]]


class ChatSessionSummary(BaseModel):
    session_id: str
    last_message: str
    last_role: str
    last_updated: datetime
    message_count: int


class ChatSessionsResponse(BaseModel):
    user_id: str
    sessions: list[ChatSessionSummary]
