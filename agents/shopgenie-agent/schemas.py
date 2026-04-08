from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ShopGenieActionRequest(BaseModel):
    taskId: str | None = None
    userId: str | None = None
    agentId: str | None = None

    action: str = Field(min_length=1)
    query: str | None = None
    prompt: str | None = None
    budget: str | None = None
    recipientEmail: str | None = None
    sendEmail: bool | None = None

    model_config = ConfigDict(extra="allow")


class ShopProduct(BaseModel):
    name: str
    price: str | None = None
    why: str | None = None
    source: str | None = None


class ShopGenieResult(BaseModel):
    query: str
    bestProduct: str
    why: str
    reasoning: str
    youtubeReview: str | None = None
    emailSent: bool = False
    emailStatus: str | None = None
    products: list[ShopProduct] = Field(default_factory=list)


class ShopGenieActionResponse(BaseModel):
    status: str
    type: str
    message: str | None = None
    summary: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    displayName: str | None = None
