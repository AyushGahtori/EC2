"""Pydantic request/response schemas for strata-agent."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrataAttachment(BaseModel):
    name: str
    mimeType: str | None = None
    size: int | None = None
    extractedText: str | None = None
    storagePath: str | None = None
    downloadUrl: str | None = None


class StrataActionRequest(BaseModel):
    taskId: str | None = None
    userId: str | None = None
    agentId: str | None = None
    action: str = Field(min_length=1)

    symbol: str | None = None
    month: int | None = None
    months: int | None = None
    question: str | None = None
    context: dict[str, Any] | None = None
    reportName: str | None = None
    attachments: list[StrataAttachment] | None = None
    model_config = ConfigDict(extra="allow")


class StrataActionResponse(BaseModel):
    status: str
    type: str | None = None
    message: str | None = None
    summary: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    displayName: str | None = None
