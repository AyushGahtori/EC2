from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class LocationInput(BaseModel):
    location_name: str = ""
    lat: float | None = None
    lng: float | None = None


class BuildingConstructionActionRequest(BaseModel):
    taskId: str | None = None
    userId: str | None = None
    agentId: str | None = None
    chatId: str | None = None
    action: str

    message: str | None = None
    prompt: str | None = None
    location: LocationInput | None = None
    image_b64: str | None = None

    budget_inr: float | None = None
    floors: int | None = None
    rooms: list[str] = Field(default_factory=list)
    design_style: str | None = None
    special_requirements: str | None = None
    vendor_type: str | None = None


class BuildingConstructionActionResponse(BaseModel):
    status: Literal["success", "partial_success", "failed", "needs_input", "action_required"] = "success"
    type: str = "building_construction_result"
    displayName: str = "Building Construction Agent"
    message: str | None = None
    summary: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
