from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CyberSocActionRequest(BaseModel):
    taskId: str | None = None
    userId: str | None = None
    agentId: str | None = None
    chatId: str | None = None
    action: str = Field(default="list_capabilities", min_length=1)

    log: str | None = None
    prompt: str | None = None
    limit: int | None = None
    channels: list[str] | str | None = None
    forceRefresh: bool = False

    model_config = ConfigDict(extra="allow")


class CyberSocActionResponse(BaseModel):
    status: Literal["success", "partial_success", "failed", "needs_input", "action_required"] = "success"
    type: str = "cyber_soc_result"
    displayName: str = "Cyber AI SOC"
    message: str | None = None
    summary: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    model_config = ConfigDict(extra="allow")

