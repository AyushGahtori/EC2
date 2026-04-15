from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CandidateSnapshot(BaseModel):
    candidateId: str | None = None
    name: str | None = None
    email: str | None = None
    resumeText: str | None = None
    summary: str | None = None
    skills: list[str] = Field(default_factory=list)
    experienceYears: float | None = None
    currentRole: str | None = None
    location: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ATSActionRequest(BaseModel):
    taskId: str | None = None
    userId: str | None = None
    agentId: str | None = None
    chatId: str | None = None
    action: str

    jobTitle: str | None = None
    jobDescription: str | None = None
    candidateId: str | None = None
    candidateName: str | None = None
    candidateEmail: str | None = None
    resumeText: str | None = None
    transcript: str | None = None
    interviewStage: str | None = None
    notes: str | None = None

    candidates: list[CandidateSnapshot] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class ATSActionResponse(BaseModel):
    status: Literal["success", "partial_success", "failed", "needs_input", "action_required"] = "success"
    type: str = "ats_result"
    displayName: str = "ATS Agent"
    message: str | None = None
    summary: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
