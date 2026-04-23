from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Query

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.main import app  # noqa: E402
from ec2_shared.api_security import apply_api_security  # noqa: E402
from schemas import CyberSocActionRequest, CyberSocActionResponse  # noqa: E402
from service import capabilities_response, health_payload, run_cyber_soc_action  # noqa: E402

load_dotenv(BASE_DIR / ".env")
apply_api_security(app)


@app.get("/cybersoc/health")
@app.get("/cyber-soc/health")
def cybersoc_health() -> dict[str, object]:
    return health_payload()


@app.post("/cybersoc/action", response_model=CyberSocActionResponse)
@app.post("/cyber-soc/action", response_model=CyberSocActionResponse)
async def cybersoc_action(req: CyberSocActionRequest) -> CyberSocActionResponse:
    return await run_cyber_soc_action(req)


@app.post("/cybersoc/analyze", response_model=CyberSocActionResponse)
@app.post("/cyber-soc/analyze", response_model=CyberSocActionResponse)
async def cybersoc_analyze(req: CyberSocActionRequest) -> CyberSocActionResponse:
    payload = CyberSocActionRequest(
        taskId=req.taskId,
        userId=req.userId,
        agentId=req.agentId,
        chatId=req.chatId,
        action="analyze_log",
        log=req.log or req.prompt,
        forceRefresh=req.forceRefresh,
    )
    return await run_cyber_soc_action(payload)


@app.get("/cybersoc/history", response_model=CyberSocActionResponse)
@app.get("/cyber-soc/history", response_model=CyberSocActionResponse)
async def cybersoc_history() -> CyberSocActionResponse:
    payload = CyberSocActionRequest(action="get_history")
    return await run_cyber_soc_action(payload)


@app.get("/cybersoc/dashboard", response_model=CyberSocActionResponse)
@app.get("/cyber-soc/dashboard", response_model=CyberSocActionResponse)
async def cybersoc_dashboard() -> CyberSocActionResponse:
    payload = CyberSocActionRequest(action="dashboard_overview")
    return await run_cyber_soc_action(payload)


@app.get("/cybersoc/logs/realtime", response_model=CyberSocActionResponse)
@app.get("/cyber-soc/logs/realtime", response_model=CyberSocActionResponse)
async def cybersoc_logs_realtime(
    limit: int = Query(default=10, ge=1, le=200),
    channels: str | None = Query(default=None),
) -> CyberSocActionResponse:
    payload = CyberSocActionRequest(action="fetch_windows_logs", limit=limit, channels=channels)
    return await run_cyber_soc_action(payload)


@app.get("/cybersoc/logs/channels", response_model=CyberSocActionResponse)
@app.get("/cyber-soc/logs/channels", response_model=CyberSocActionResponse)
async def cybersoc_logs_channels() -> CyberSocActionResponse:
    payload = CyberSocActionRequest(action="list_windows_channels")
    return await run_cyber_soc_action(payload)


@app.get("/cybersoc/capabilities", response_model=CyberSocActionResponse)
@app.get("/cyber-soc/capabilities", response_model=CyberSocActionResponse)
def cybersoc_capabilities() -> CyberSocActionResponse:
    return capabilities_response()


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8043"))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)

