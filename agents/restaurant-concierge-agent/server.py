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

from ec2_shared.api_security import apply_api_security
from schemas import RestaurantConciergeActionRequest, RestaurantConciergeActionResponse
from service import DISPLAY_NAME, run_restaurant_concierge_action

load_dotenv(BASE_DIR / ".env")

app = FastAPI(
    title="Restaurant Concierge Agent API",
    description="Restaurant ordering, menu discovery, and session-based concierge runtime.",
    version="1.0.0",
)

apply_api_security(app)


@app.get("/health")
@app.get("/restaurant/health")
@app.get("/restaurant-concierge/health")
@app.get("/restaurant-concierge-agent/health")
def health() -> dict[str, str]:
    return {
        "status": "healthy",
        "agent": "restaurant-concierge-agent",
        "displayName": DISPLAY_NAME,
        "version": "1.0.0",
    }


@app.post("/action", response_model=RestaurantConciergeActionResponse)
@app.post("/restaurant/action", response_model=RestaurantConciergeActionResponse)
@app.post("/restaurant-concierge/action", response_model=RestaurantConciergeActionResponse)
@app.post("/restaurant-concierge-agent/action", response_model=RestaurantConciergeActionResponse)
async def restaurant_action(req: RestaurantConciergeActionRequest) -> RestaurantConciergeActionResponse:
    return await run_restaurant_concierge_action(req)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8044"))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
