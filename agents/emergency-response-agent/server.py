import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from emergency_agent import EmergencyAgentError, EmergencyResponseAgent

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

app = FastAPI(
    title="SnitchX Emergency Response Agent",
    description="Medical emergency triage, nearby hospital discovery, and emergency sharing payloads.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class EmergencyActionRequest(BaseModel):
    taskId: str | None = None
    userId: str | None = None
    agentId: str | None = None
    action: str
    description: str | None = None
    lat: float | None = None
    lng: float | None = None
    radius: int | None = None


class EmergencyActionResponse(BaseModel):
    status: str
    type: str | None = None
    message: str | None = None
    displayName: str | None = None
    result: dict | None = None
    error: str | None = None


@app.get("/health")
def health():
    return {"status": "healthy", "agent": "emergency-response-agent"}


@app.post("/emergency/action", response_model=EmergencyActionResponse)
def emergency_action(req: EmergencyActionRequest) -> EmergencyActionResponse:
    try:
        agent = EmergencyResponseAgent()
        action = req.action.strip().lower()

        if action == "assess_emergency":
            if not req.description:
                return EmergencyActionResponse(status="failed", error="description is required")

            result = agent.assess_emergency(req.description)
            return EmergencyActionResponse(
                status="success",
                type="emergency_triage",
                message="Emergency triage generated. Press Emergency to start live response.",
                displayName="Emergency Triage",
                result=result,
            )

        if action == "activate_emergency":
            if req.lat is None or req.lng is None:
                return EmergencyActionResponse(status="failed", error="lat and lng are required")

            result = agent.activate_emergency(
                lat=req.lat,
                lng=req.lng,
                description=req.description or "",
                radius=req.radius or 5000,
            )
            return EmergencyActionResponse(
                status="success",
                type="emergency_response",
                message="Emergency response activated. Live location and nearby hospitals loaded.",
                displayName="Emergency Response",
                result=result,
            )

        return EmergencyActionResponse(status="failed", error=f"Unknown action: {req.action}")
    except EmergencyAgentError as exc:
        return EmergencyActionResponse(status="failed", error=str(exc))
    except Exception as exc:
        return EmergencyActionResponse(status="failed", error=f"Emergency agent failed: {exc}")


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8510"))
    uvicorn.run(app, host="0.0.0.0", port=port)
