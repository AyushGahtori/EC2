from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class LMSActionRequest(BaseModel):
    taskId: str | None = None
    userId: str | None = None
    agentId: str | None = None
    chatId: str | None = None
    action: str

    prompt: str | None = None
    learnerId: str | None = None
    learnerName: str | None = None
    department: str | None = None
    dateRange: str | None = None
    courseType: str | None = None
    enrollmentType: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class LMSActionResponse(BaseModel):
    status: Literal["success", "partial_success", "failed", "needs_input", "action_required"] = "success"
    type: str = "lms_dashboard_result"
    displayName: str = "LMS Agent"
    message: str | None = None
    summary: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
