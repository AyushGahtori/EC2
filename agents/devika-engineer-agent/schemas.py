from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class DevikaEngineerActionRequest(BaseModel):
    taskId: str | None = None
    userId: str | None = None
    agentId: str | None = None
    chatId: str | None = None
    sessionId: str | None = None
    action: str

    prompt: str | None = None
    objective: str | None = None
    featureRequest: str | None = None
    question: str | None = None
    errorLog: str | None = None
    stackTrace: str | None = None
    codeSnippet: str | None = None
    codebaseSummary: str | None = None
    projectName: str | None = None
    repositoryUrl: str | None = None
    branch: str | None = None
    files: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    forceRefresh: bool = False


class DevikaEngineerActionResponse(BaseModel):
    status: Literal["success", "partial_success", "failed", "needs_input", "action_required"] = "success"
    type: str = "devika_plan_result"
    displayName: str = "Devika Engineer Agent"
    message: str | None = None
    summary: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
