from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class TravelHalperActionRequest(BaseModel):
    taskId: str | None = None
    userId: str | None = None
    agentId: str | None = None
    chatId: str | None = None

    action: str
    prompt: str | None = None
    threadId: str | None = None

    senderEmail: str | None = None
    receiverEmail: str | None = None
    subject: str | None = None


class TravelHalperActionResponse(BaseModel):
    status: Literal["success", "partial_success", "failed", "needs_input", "action_required"] = "success"
    type: str = "travel_plan_result"
    displayName: str = "Travel Halper"
    message: str | None = None
    summary: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
