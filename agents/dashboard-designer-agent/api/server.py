from __future__ import annotations

import os
import re

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .dashboard_service import build_dashboard_artifact
from .firestore_store import save_dashboard_artifact
from .schemas import DashboardDesignerActionRequest, DashboardDesignerActionResponse

AGENT_SLUG = "dashboard-designer-agent"
DISPLAY_NAME = "Dashboard Designer"
SUPPORTED_ACTIONS = {"generate_dashboard", "refine_dashboard", "design_dashboard", "update_dashboard"}
UID_PATTERN = re.compile(r"^[A-Za-z0-9._:@-]{3,128}$")

app = FastAPI(
    title="Dashboard Designer Agent API",
    description="Transforms prompts and optional structured data into typed dashboard schemas.",
    version="1.0.0",
)

allow_origins = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "*").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy", "agent": AGENT_SLUG, "displayName": DISPLAY_NAME, "version": "1.0.0"}


@app.get("/dashboarddesigner/health")
def dashboarddesigner_health() -> dict[str, str]:
    return health()


@app.post("/dashboarddesigner/action", response_model=DashboardDesignerActionResponse)
async def dashboard_designer_action(req: DashboardDesignerActionRequest) -> DashboardDesignerActionResponse:
    action = (req.action or "").strip().lower()
    if action not in SUPPORTED_ACTIONS:
        return DashboardDesignerActionResponse(
            status="failed",
            type="dashboard_designer_result",
            message="Dashboard Designer supports generate_dashboard, refine_dashboard, design_dashboard, or update_dashboard.",
            displayName=DISPLAY_NAME,
            error="Unsupported action.",
            error_code="UNSUPPORTED_ACTION",
            recommended_next_actions=["Use generate_dashboard, refine_dashboard, design_dashboard, or update_dashboard."],
        )

    prompt = (req.prompt or "").strip()
    project_context = (req.projectContext or "").strip()
    if req.userId:
        normalized_uid = req.userId.strip()
        if "/" in normalized_uid or not UID_PATTERN.match(normalized_uid):
            return DashboardDesignerActionResponse(
                status="failed",
                type="dashboard_designer_result",
                message="Dashboard Designer requires a valid authenticated userId for saved dashboard history.",
                displayName=DISPLAY_NAME,
                error="Invalid userId format for Firestore-scoped storage.",
                error_code="INVALID_USER_ID",
            )
    else:
        normalized_uid = None

    if not prompt and not project_context and req.manualData is None and not req.existingDashboard:
        return DashboardDesignerActionResponse(
            status="failed",
            type="dashboard_designer_result",
            message="Please provide a prompt, context, manual data, or an existing dashboard to refine.",
            displayName=DISPLAY_NAME,
            error="A dashboard prompt or data payload is required.",
            error_code="MISSING_INPUT",
            recommended_next_actions=["Describe the dashboard you want to build."],
        )

    try:
        artifact = await build_dashboard_artifact(
            prompt=prompt,
            project_context=project_context,
            manual_data=req.manualData,
            existing_dashboard=req.existingDashboard,
            dashboard_theme=req.dashboardTheme,
            time_horizon=req.timeHorizon,
            audience=req.audience,
            context=req.context or {},
        )

        if normalized_uid:
            await save_dashboard_artifact(
                normalized_uid,
                {
                    "artifactId": f"{req.agentId or AGENT_SLUG}-{artifact.dashboardSchema.title.lower().replace(' ', '-')}",
                    "agentId": req.agentId or AGENT_SLUG,
                    "displayName": DISPLAY_NAME,
                    "status": "success",
                    "summary": artifact.summary,
                    "prompt": prompt,
                    "projectContext": project_context,
                    "dashboardSchema": artifact.dashboardSchema.model_dump(),
                    "requestSummary": artifact.requestSummary.model_dump(),
                    "analysis": artifact.analysis.model_dump(),
                },
            )

        return DashboardDesignerActionResponse(
            status="success",
            type="dashboard_designer_result",
            message="Dashboard schema generated successfully.",
            displayName=DISPLAY_NAME,
            summary=artifact.summary,
            result=artifact,
            recommended_next_actions=artifact.recommendedNextActions,
            ui_payload=artifact.dashboardSchema.model_dump(),
            internal_payload={
                "requestSummary": artifact.requestSummary.model_dump(),
                "analysis": artifact.analysis.model_dump(),
                "sources": artifact.sources,
            },
        )
    except ValueError as exc:
        return DashboardDesignerActionResponse(
            status="failed",
            type="dashboard_designer_result",
            message="Dashboard Designer could not understand the request.",
            displayName=DISPLAY_NAME,
            error=str(exc),
            error_code="INVALID_REQUEST",
        )
    except Exception:
        return DashboardDesignerActionResponse(
            status="failed",
            type="dashboard_designer_result",
            message="Dashboard Designer ran into a problem while generating the dashboard schema.",
            displayName=DISPLAY_NAME,
            error="Unable to generate dashboard schema right now.",
            error_code="DASHBOARD_GENERATION_FAILED",
            recommended_next_actions=["Try again with a simpler prompt or provide structured data."],
        )
