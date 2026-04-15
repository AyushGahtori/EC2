from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from schemas import ATSActionRequest, ATSActionResponse
from service import run_ats_action

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

app = FastAPI(
    title="ATS Agent API",
    description="Recruiting workflow support for candidate analysis, interview planning, and comparison.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "healthy",
        "agent": "ats-agent",
        "displayName": "ATS Agent",
        "version": "1.0.0",
    }


@app.get("/ats-agent/health")
@app.get("/ats/health")
def ats_health() -> dict[str, str]:
    return health()


@app.post("/ats-agent/action", response_model=ATSActionResponse)
@app.post("/ats/action", response_model=ATSActionResponse)
async def ats_action(req: ATSActionRequest) -> ATSActionResponse:
    return await run_ats_action(req)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8036"))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
