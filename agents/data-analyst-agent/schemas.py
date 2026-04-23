from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DataAnalystActionRequest(BaseModel):
    taskId: str | None = None
    userId: str | None = None
    agentId: str | None = None
    chatId: str | None = None
    action: str = Field(min_length=1)

    data: list[Any] | None = None
    label: str | None = None
    goal: str | None = None
    prompt: str | None = None

    forceRefresh: bool = False
    stream: bool = False

    model_config = ConfigDict(extra="allow")


class MonitorRequest(BaseModel):
    data: list[Any]
    label: str | None = None
    userId: str | None = None
    taskId: str | None = None
    forceRefresh: bool = False


class AutonomousRequest(BaseModel):
    goal: str | None = None
    prompt: str | None = None
    data: list[Any] | None = None
    label: str | None = None
    userId: str | None = None
    taskId: str | None = None
    forceRefresh: bool = False


class DataAnomalyResult(BaseModel):
    status: Literal["anomaly", "ok"]
    indices: list[int]
    flaggedValues: list[float]
    message: str
    zscoreIndices: list[int]
    isolationIndices: list[int]
    confidence: Literal["high", "medium", "low"]
    stats: dict[str, float | int]


class DataAnalystActionResponse(BaseModel):
    status: Literal["success", "partial_success", "failed", "needs_input", "action_required"] = "success"
    type: str = "data_analyst_result"
    message: str | None = None
    summary: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    displayName: str = "Data Analyst Agent"

    model_config = ConfigDict(extra="allow")
