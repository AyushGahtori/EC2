from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SmartGTMActionRequest(BaseModel):
    taskId: str | None = None
    userId: str | None = None
    agentId: str | None = None
    action: str = Field(min_length=1)
    mode: str | None = None
    query: str | None = None
    url: str | None = None
    companyUrl: str | None = None
    companyName: str | None = None
    forceFresh: bool | None = None
    reportFormat: str | None = None
    model_config = ConfigDict(extra="allow")


class SmartGTMSource(BaseModel):
    kind: str
    title: str
    url: str | None = None
    excerpt: str | None = None


class SmartGTMSection(BaseModel):
    title: str
    summary: str
    bullets: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class SmartGTMReport(BaseModel):
    slug: Literal["smart-gtm-agent"] = "smart-gtm-agent"
    companyName: str
    companyUrl: str | None = None
    mode: Literal["research", "gtm", "channel"]
    modeLabel: str
    cached: bool = False
    cachedAt: str | None = None
    generatedAt: str
    sourceStatus: dict[str, str] = Field(default_factory=dict)
    companySignals: list[str] = Field(default_factory=list)
    competitorSignals: list[str] = Field(default_factory=list)
    keyTakeaways: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    nextSteps: list[str] = Field(default_factory=list)
    sources: list[SmartGTMSource] = Field(default_factory=list)
    sections: list[SmartGTMSection] = Field(default_factory=list)
    reportMarkdown: str


class SmartGTMActionResponse(BaseModel):
    status: str
    type: str
    message: str | None = None
    summary: str | None = None
    result: SmartGTMReport | None = None
    error: str | None = None
    displayName: str | None = None
    model_config = ConfigDict(extra="allow")

