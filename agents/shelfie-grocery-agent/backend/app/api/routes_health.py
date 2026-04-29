from datetime import datetime, timezone

from fastapi import APIRouter, Request


router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(request: Request) -> dict:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "provider": settings.LLM_PROVIDER,
        "model": settings.active_model,
    }

