"""Analysis routes - /analyze, /history, /health endpoints."""
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, validator

from app.agent import CyberAgent
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter()

# Singleton shared across routes
agent = CyberAgent()


class AnalyzeRequest(BaseModel):
    log: str

    @validator("log")
    def validate_log(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Log input cannot be empty")
        if len(v) > 10_000:
            raise ValueError("Log input too large (max 10,000 chars)")
        return v


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "app": settings.app_name,
        "vt_configured": bool(settings.virustotal_api_key),
        "llm_configured": bool(settings.ollama_base_url) and bool(settings.ollama_model),
    }


@router.post("/analyze")
async def analyze(req: AnalyzeRequest):
    try:
        result = await agent.analyze(req.log)
        return JSONResponse(content=result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Analysis error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal analysis error")


@router.get("/history")
async def history():
    h = agent.get_history()
    return {"history": h, "count": len(h)}
