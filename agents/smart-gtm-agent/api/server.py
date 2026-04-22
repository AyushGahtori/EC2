from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from ec2_shared.api_security import apply_api_security

from schemas import SmartGTMActionRequest, SmartGTMActionResponse
from service import DISPLAY_NAME, execute_smart_gtm

logger = logging.getLogger(__name__)

app = FastAPI(title="Smart GTM Agent API", description="Company research, go-to-market, and channel strategy action server.", version="1.0.0")

apply_api_security(app)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy", "agent": "smart-gtm-agent", "displayName": DISPLAY_NAME, "version": "1.0.0"}


@app.get("/smartgtm/health")
def smartgtm_health() -> dict[str, str]:
    return health()


@app.post("/smart-gtm-agent/action", response_model=SmartGTMActionResponse)
@app.post("/smartgtm/action", response_model=SmartGTMActionResponse)
async def smart_gtm_action(req: SmartGTMActionRequest) -> SmartGTMActionResponse:
    try:
        return execute_smart_gtm(req)
    except Exception as exc:  # pragma: no cover
        logger.exception("Smart GTM request failed")
        return SmartGTMActionResponse(status="failed", type="smart_gtm_result", error=f"smart-gtm-agent failed: {exc.__class__.__name__}", message="Smart GTM Agent could not complete that request.", displayName=DISPLAY_NAME)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8033"))
    uvicorn.run(app, host="0.0.0.0", port=port)
