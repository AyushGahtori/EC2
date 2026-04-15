from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from schemas import LMSActionRequest, LMSActionResponse
from service import run_lms_action

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

app = FastAPI(
    title="LMS Agent API",
    description="Learning management dashboard and training orchestration assistant.",
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
        "agent": "lms-agent",
        "displayName": "LMS Agent",
        "version": "1.0.0",
    }


@app.get("/lms/health")
def lms_health() -> dict[str, str]:
    return health()


@app.post("/lms/action", response_model=LMSActionResponse)
async def lms_action(req: LMSActionRequest) -> LMSActionResponse:
    return await run_lms_action(req)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8039"))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
