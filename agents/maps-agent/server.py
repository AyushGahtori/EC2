import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from maps_agent import MapsAgent, MapsAgentError

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

app = FastAPI(
    title="SnitchX Maps Agent",
    description="Directions, place search, geocoding, and travel-time calculations.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class MapsActionRequest(BaseModel):
    taskId: str | None = None
    userId: str | None = None
    agentId: str | None = None
    action: str
    origin: str | None = None
    destination: str | None = None
    mode: str | None = None
    query: str | None = None
    location: str | None = None
    radius: int | None = None
    address: str | None = None
    latlng: str | None = None
    origins: str | None = None
    destinations: str | None = None


class MapsActionResponse(BaseModel):
    status: str
    type: str | None = None
    message: str | None = None
    summary: str | None = None
    result: dict | None = None
    error: str | None = None
    displayName: str | None = None


@app.get("/health")
def health():
    return {"status": "healthy", "agent": "maps-agent"}


@app.post("/maps/action", response_model=MapsActionResponse)
def maps_action(req: MapsActionRequest) -> MapsActionResponse:
    try:
        agent = MapsAgent()
        action = req.action.strip().lower()

        if action == "get_directions":
            if not req.origin or not req.destination:
                return MapsActionResponse(status="failed", error="origin and destination are required")
            result = agent.get_directions(req.origin, req.destination, req.mode or "driving")
            summary = (
                f"{result['distance']} via {result['summary']} in about {result['duration']} "
                f"from {result['start_address']} to {result['end_address']}."
            )
            return MapsActionResponse(
                status="success",
                type="maps_directions",
                message="Directions ready.",
                summary=summary,
                result=result,
                displayName="Directions",
            )

        if action == "search_places":
            if not req.query:
                return MapsActionResponse(status="failed", error="query is required")
            result = agent.search_places(req.query, req.location, req.radius)
            names = [place["name"] for place in result["results"][:5] if place.get("name")]
            return MapsActionResponse(
                status="success",
                type="maps_places",
                message=f"Found {len(result['results'])} place(s).",
                summary=", ".join(names) if names else "No matching places found.",
                result=result,
                displayName="Places",
            )

        if action == "geocode":
            if not req.address and not req.latlng:
                return MapsActionResponse(status="failed", error="address or latlng is required")
            result = agent.geocode(req.address, req.latlng)
            return MapsActionResponse(
                status="success",
                type="maps_geocode",
                message="Location resolved.",
                summary=result.get("formatted_address", ""),
                result=result,
                displayName="Geocode",
            )

        if action == "distance_matrix":
            if not req.origins or not req.destinations:
                return MapsActionResponse(status="failed", error="origins and destinations are required")
            result = agent.distance_matrix(req.origins, req.destinations, req.mode or "driving")
            return MapsActionResponse(
                status="success",
                type="maps_distance_matrix",
                message="Travel matrix ready.",
                summary="Calculated travel distance and duration for the requested route matrix.",
                result=result,
                displayName="Distance Matrix",
            )

        return MapsActionResponse(status="failed", error=f"Unknown action: {req.action}")
    except MapsAgentError as exc:
        return MapsActionResponse(status="failed", error=str(exc))
    except Exception as exc:
        return MapsActionResponse(status="failed", error=f"Maps agent failed: {exc}")


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8500"))
    uvicorn.run(app, host="0.0.0.0", port=port)
