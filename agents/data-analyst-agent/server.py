from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI
from sse_starlette.sse import EventSourceResponse

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ec2_shared.api_security import apply_api_security
from schemas import (
    AutonomousRequest,
    DataAnalystActionRequest,
    DataAnalystActionResponse,
    MonitorRequest,
)
from service import DISPLAY_NAME, run_data_analyst_action

load_dotenv(BASE_DIR / ".env")

STREAM_TOKEN_DELAY = max(0, int((os.getenv("DATA_ANALYST_STREAM_TOKEN_DELAY_MS") or "25").strip() or "25")) / 1000.0

app = FastAPI(
    title="Data Analyst Agent API",
    description="Anomaly detection and autonomous data-quality analysis agent.",
    version="1.0.0",
)

apply_api_security(app)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "healthy",
        "agent": "data-analyst-agent",
        "displayName": DISPLAY_NAME,
        "version": "1.0.0",
    }


@app.get("/dataanalyst/health")
@app.get("/data-analyst/health")
def dataanalyst_health() -> dict[str, str]:
    return health()


@app.post("/dataanalyst/action", response_model=DataAnalystActionResponse)
@app.post("/data-analyst/action", response_model=DataAnalystActionResponse)
@app.post("/data-analyst-agent/action", response_model=DataAnalystActionResponse)
async def data_analyst_action(req: DataAnalystActionRequest) -> DataAnalystActionResponse:
    return await run_data_analyst_action(req)


@app.post("/dataanalyst/monitor", response_model=DataAnalystActionResponse)
@app.post("/data-analyst/monitor", response_model=DataAnalystActionResponse)
async def dataanalyst_monitor(req: MonitorRequest) -> DataAnalystActionResponse:
    payload = DataAnalystActionRequest(
        taskId=req.taskId,
        userId=req.userId,
        action="monitor",
        data=req.data,
        label=req.label,
        forceRefresh=req.forceRefresh,
    )
    return await run_data_analyst_action(payload)


@app.post("/dataanalyst/autonomous", response_model=DataAnalystActionResponse)
@app.post("/data-analyst/autonomous", response_model=DataAnalystActionResponse)
async def dataanalyst_autonomous(req: AutonomousRequest) -> DataAnalystActionResponse:
    payload = DataAnalystActionRequest(
        taskId=req.taskId,
        userId=req.userId,
        action="autonomous",
        goal=req.goal,
        prompt=req.prompt,
        data=req.data,
        label=req.label,
        forceRefresh=req.forceRefresh,
    )
    return await run_data_analyst_action(payload)


@app.post("/dataanalyst/monitor/stream")
@app.post("/data-analyst/monitor/stream")
async def dataanalyst_monitor_stream(req: MonitorRequest) -> EventSourceResponse:
    async def event_generator() -> AsyncGenerator[dict[str, str], None]:
        yield {"event": "status", "data": json.dumps({"step": "start", "message": "Starting monitor pipeline."})}
        await asyncio.sleep(0.02)
        yield {"event": "status", "data": json.dumps({"step": "detect", "message": "Running anomaly detection."})}

        payload = DataAnalystActionRequest(
            taskId=req.taskId,
            userId=req.userId,
            action="monitor",
            data=req.data,
            label=req.label,
            forceRefresh=req.forceRefresh,
        )
        response = await run_data_analyst_action(payload)

        if response.status not in {"success", "partial_success"}:
            yield {
                "event": "error",
                "data": json.dumps(
                    {
                        "status": response.status,
                        "message": response.message,
                        "error": response.error,
                    }
                ),
            }
            return

        summary = str((response.result or {}).get("insight", {}).get("summary") or response.summary or "").strip()
        if summary:
            for token in summary.split():
                yield {"event": "token", "data": json.dumps({"token": f"{token} "})}
                if STREAM_TOKEN_DELAY:
                    await asyncio.sleep(STREAM_TOKEN_DELAY)

        yield {"event": "done", "data": json.dumps(response.model_dump(mode="json"))}

    return EventSourceResponse(event_generator())


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8042"))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
