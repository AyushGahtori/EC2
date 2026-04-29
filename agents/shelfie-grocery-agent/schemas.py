from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ShelfieActionRequest(BaseModel):
    taskId: str | None = None
    userId: str | None = None
    agentId: str | None = None
    chatId: str | None = None

    action: str = Field(default="run_shelfie_grocery_agent", min_length=1)
    prompt: str | None = None
    message: str | None = None
    query: str | None = None

    session_id: str | None = None
    sessionId: str | None = None
    limit: int | None = None

    forceRefresh: bool = False

    model_config = ConfigDict(extra="allow")


class ShelfieActionResponse(BaseModel):
    status: Literal["success", "partial_success", "failed", "needs_input", "action_required"] = "success"
    type: str = "shelfie_grocery_result"
    message: str | None = None
    summary: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    displayName: str = "Shelfie Grocery Agent"

    model_config = ConfigDict(extra="allow")
