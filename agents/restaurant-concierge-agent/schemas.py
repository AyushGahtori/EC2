from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ActionStatus = Literal["success", "partial_success", "needs_input", "failed", "action_required"]


class RestaurantConciergeActionRequest(BaseModel):
    taskId: str | None = None
    userId: str | None = None
    agentId: str | None = None
    chatId: str | None = None
    sessionId: str | None = None
    action: str = Field(default="run_restaurant_concierge")
    prompt: str | None = None
    message: str | None = None
    query: str | None = None
    itemName: str | None = None
    category: str | None = None
    dietaryFilter: str | None = None
    reason: str | None = None
    forceRefresh: bool = False
    forceReset: bool = False
    context: dict[str, Any] | None = None


class RestaurantConciergeActionResponse(BaseModel):
    status: ActionStatus
    type: str
    displayName: str
    message: str
    summary: str
    result: dict[str, Any] | None = None
    error: str | None = None
