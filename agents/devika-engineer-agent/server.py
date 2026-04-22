from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from schemas import DevikaEngineerActionRequest, DevikaEngineerActionResponse
from service import run_devika_engineer_action
from ec2_shared.api_security import apply_api_security

load_dotenv(BASE_DIR / ".env")

app = FastAPI(
    title="Devika Engineer Agent API",
    description="Software engineering copilot for planning, feature delivery, debugging, and deployment strategy.",
    version="1.0.0",
)

apply_api_security(app)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "healthy",
        "agent": "devika-engineer-agent",
        "displayName": "Devika Engineer Agent",
        "version": "1.0.0",
    }


@app.get("/devika/health")
def devika_health() -> dict[str, str]:
    return health()


@app.post("/devika/action", response_model=DevikaEngineerActionResponse)
async def devika_action(req: DevikaEngineerActionRequest) -> DevikaEngineerActionResponse:
    return await run_devika_engineer_action(req)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8041"))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
