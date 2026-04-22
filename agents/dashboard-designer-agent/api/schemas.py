from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class DashboardDesignerActionRequest(BaseModel):
    taskId: Optional[str] = None
    userId: Optional[str] = None
    agentId: Optional[str] = None
    action: str = Field(..., description="generate_dashboard or refine_dashboard")
    prompt: Optional[str] = None
    projectContext: Optional[str] = None
    manualData: Optional[Any] = None
    context: Dict[str, Any] = Field(default_factory=dict)
    existingDashboard: Optional[Dict[str, Any]] = None
    dashboardTheme: Optional[str] = None
    timeHorizon: Optional[str] = None
    audience: Optional[str] = None


class DashboardKPI(BaseModel):
    title: str
    value: str
    change: float = 0.0
    changeLabel: str = ""
    trend: Literal["up", "down", "flat"] = "flat"
    format: Literal["currency", "number", "percent", "ratio", "compact"] = "compact"
    note: str = ""


class DashboardChart(BaseModel):
    id: str
    type: Literal["line", "bar", "pie", "area", "radar"]
    title: str
    subtitle: str = ""
    xKey: str = "label"
    dataKeys: List[str] = Field(default_factory=list)
    data: List[Dict[str, Any]] = Field(default_factory=list)
    insight: str = ""


class DashboardTableColumn(BaseModel):
    key: str
    label: str


class DashboardTable(BaseModel):
    id: str
    title: str
    subtitle: str = ""
    columns: List[DashboardTableColumn] = Field(default_factory=list)
    data: List[Dict[str, Any]] = Field(default_factory=list)


class DashboardThreshold(BaseModel):
    metric_name: str
    operator: str
    threshold_value: float
    severity: Literal["warning", "critical"] = "warning"
    enabled: bool = True
    message: str = ""


class DashboardRequestSummary(BaseModel):
    prompt: str
    project_context_present: bool = False
    manual_data_present: bool = False
    domain: str = "general"
    audience: str = "operators"
    time_horizon: str = "monthly"
    focus: str = "performance"
    data_shape: str = "synthetic"


class DashboardAnalysis(BaseModel):
    key_metrics: List[str] = Field(default_factory=list)
    key_signals: List[str] = Field(default_factory=list)
    chart_rationale: List[str] = Field(default_factory=list)
    threshold_rationale: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    confidence: float = 0.75


class DashboardSchema(BaseModel):
    title: str
    description: str
    audience: str
    time_horizon: str
    theme: str
    summary: str
    cards: List[DashboardKPI] = Field(default_factory=list)
    kpis: List[DashboardKPI] = Field(default_factory=list)
    charts: List[DashboardChart] = Field(default_factory=list)
    tables: List[DashboardTable] = Field(default_factory=list)
    thresholds: List[DashboardThreshold] = Field(default_factory=list)
    highlights: List[str] = Field(default_factory=list)


class DashboardDesignerArtifact(BaseModel):
    artifactType: Literal["dashboard_schema"] = "dashboard_schema"
    title: str
    subtitle: str
    summary: str
    dashboardSchema: DashboardSchema
    requestSummary: DashboardRequestSummary
    analysis: DashboardAnalysis
    recommendedNextActions: List[str] = Field(default_factory=list)
    sources: List[Dict[str, Any]] = Field(default_factory=list)


class DashboardDesignerActionResponse(BaseModel):
    status: str
    type: str = Field(default="dashboard_designer_result")
    message: Optional[str] = None
    displayName: str = Field(default="Dashboard Designer")
    summary: Optional[str] = None
    result: Optional[DashboardDesignerArtifact] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    recommended_next_actions: List[str] = Field(default_factory=list)
    ui_payload: Optional[Dict[str, Any]] = None
    internal_payload: Optional[Dict[str, Any]] = None

