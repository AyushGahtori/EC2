"""
Pydantic schemas for API requests/responses and MongoDB documents.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────────

class ContentType(str, Enum):
    DESCRIPTION = "description"
    POSTER = "poster"
    SOCIAL_POST = "social_post"
    HASHTAGS = "hashtags"
    CAMPAIGN = "campaign"
    AD_COPY = "ad_copy"


class Platform(str, Enum):
    INSTAGRAM = "instagram"
    LINKEDIN = "linkedin"
    TWITTER = "twitter"
    FACEBOOK = "facebook"
    PINTEREST = "pinterest"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class AgentPhase(str, Enum):
    THINKING = "thinking"
    PLANNING = "planning"
    ACTING = "acting"
    OBSERVING = "observing"
    REFLECTING = "reflecting"
    RESPONDING = "responding"


# ── API Request / Response Models ────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(..., description="User message text")
    session_id: str = Field(..., description="Chat session identifier")
    image_id: Optional[str] = Field(None, description="Previously uploaded image ID")


class SessionCreateRequest(BaseModel):
    name: Optional[str] = Field(None, description="Optional session name")
    product_name: Optional[str] = Field(None, description="Product name for context")
    brand_guidelines: Optional[str] = Field(None, description="Brand voice/style guidelines")


class UploadResponse(BaseModel):
    image_id: str
    filename: str
    content_type: str
    size: int
    preview_url: str


# ── SSE Stream Event Models ───────────────────────────────────────────────────

class StreamEvent(BaseModel):
    """Base SSE event sent to frontend."""
    event: str                    # token | phase | tool_call | tool_result | content | error | done
    data: dict[str, Any]


class TokenEvent(BaseModel):
    token: str


class PhaseEvent(BaseModel):
    phase: AgentPhase
    description: str


class ToolCallEvent(BaseModel):
    tool_name: str
    tool_args: dict[str, Any]


class ToolResultEvent(BaseModel):
    tool_name: str
    result_preview: str
    success: bool


class ContentEvent(BaseModel):
    content_type: ContentType
    content: str
    platform: Optional[Platform] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── MongoDB Document Models ───────────────────────────────────────────────────

class SessionDocument(BaseModel):
    session_id: str
    name: str = "New Session"
    product_name: Optional[str] = None
    product_image_url: Optional[str] = None
    product_analysis: Optional[str] = None
    brand_guidelines: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    message_count: int = 0


class MessageDocument(BaseModel):
    session_id: str
    role: MessageRole
    content: str
    image_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class GeneratedContentDocument(BaseModel):
    session_id: str
    content_type: ContentType
    content: str
    platform: Optional[Platform] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ProductDocument(BaseModel):
    session_id: str
    name: Optional[str] = None
    analysis: str
    image_id: str
    image_url: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ── API Response Models ───────────────────────────────────────────────────────

class SessionResponse(BaseModel):
    session_id: str
    name: str
    product_name: Optional[str]
    created_at: datetime
    message_count: int


class MessageResponse(BaseModel):
    id: str
    role: MessageRole
    content: str
    image_id: Optional[str] = None
    created_at: datetime


class ContentResponse(BaseModel):
    id: str
    content_type: ContentType
    content: str
    platform: Optional[Platform] = None
    metadata: dict[str, Any]
    created_at: datetime


class HealthResponse(BaseModel):
    status: str
    provider: str
    model: str
    redis: bool
    mongodb: bool
