from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from ec2_shared.api_security import apply_api_security

from schemas import BuildingConstructionActionRequest, BuildingConstructionActionResponse
from service import run_building_construction_action

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

app = FastAPI(
    title="Building Construction Agent API",
    description="Construction planning assistant with layout, cost, and vendor-oriented output.",
    version="1.0.0",
)

apply_api_security(app)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "healthy",
        "agent": "building-construction-agent",
        "displayName": "Building Construction Agent",
        "version": "1.0.0",
    }


@app.get("/building/health")
@app.get("/building-construction/health")
def building_health() -> dict[str, str]:
    return health()


@app.post("/building/action", response_model=BuildingConstructionActionResponse)
@app.post("/building-construction/action", response_model=BuildingConstructionActionResponse)
async def building_action(req: BuildingConstructionActionRequest) -> BuildingConstructionActionResponse:
    return await run_building_construction_action(req)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8037"))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
