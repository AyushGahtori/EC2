from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


class DiaHelperActionRequest(BaseModel):
    """Request contract for dia-helper-agent (EC2 runtime)."""

    taskId: Optional[str] = None
    userId: Optional[str] = None
    agentId: Optional[str] = None
    action: str = Field(..., description="Action to perform, e.g. generate_diagram or update_diagram.")

    prompt: Optional[str] = Field(
        default=None,
        description="Natural language description of the system or flow to diagram.",
    )

    projectContext: Optional[str] = Field(
        default=None,
        description="Longer project brief, requirements, or architecture notes.",
    )

    diagramType: Optional[str] = Field(
        default=None,
        description="Preferred Mermaid diagram type (flowchart, sequenceDiagram, stateDiagram-v2, gantt).",
    )

    fileKey: Optional[str] = Field(
        default=None,
        description="Optional external reference key (e.g. Figma file key).",
    )

    currentMermaid: Optional[str] = Field(
        default=None,
        description="Existing Mermaid diagram that should be modified instead of recreated from scratch.",
    )

    editInstruction: Optional[str] = Field(
        default=None,
        description="Incremental update instruction for modifying an existing diagram.",
    )


class DiaDiagramArtifact(BaseModel):
    title: str
    summary: str
    diagramType: str
    mermaid: str
    figmaPrompt: str
    sources: List[dict[str, Any]] = Field(default_factory=list)


class DiaHelperActionResponse(BaseModel):
    status: str
    type: Optional[str] = Field(default="dia_diagram")
    message: Optional[str] = None
    displayName: Optional[str] = Field(default="Diagram")
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None

