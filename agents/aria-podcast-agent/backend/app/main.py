"""
ARIA Podcast Agent — FastAPI Backend
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

load_dotenv()

from app.routes.chat import router as chat_router
from app.routes.tts import router as tts_router
from app.routes.voice import router as voice_router

# ──────────────────────────────────────────────
# App
# ──────────────────────────────────────────────

app = FastAPI(
    title="ARIA Podcast Agent",
    description="AI-powered podcast host and creator",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS
origins_raw = os.getenv("CORS_ORIGINS", "http://localhost:3000")
origins = [o.strip() for o in origins_raw.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(chat_router)
app.include_router(voice_router)
app.include_router(tts_router)


# ──────────────────────────────────────────────
# Startup / Shutdown
# ──────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    logger.info("ARIA starting up...")
    provider = os.getenv("LLM_PROVIDER", "ollama")
    model = os.getenv("LLM_MODEL", "gemma3:4b")
    logger.info(f"LLM: {provider}/{model}")

    # Check DB connectivity (non-fatal)
    try:
        from app.services.memory_service import health_check
        health = await health_check()
        logger.info(f"DB health: {health}")
    except Exception as exc:
        logger.warning(f"DB health check failed (non-fatal): {exc}")


@app.on_event("shutdown")
async def shutdown():
    logger.info("ARIA shutting down...")


# ──────────────────────────────────────────────
# Health
# ──────────────────────────────────────────────

@app.get("/health")
async def health():
    from app.services.memory_service import health_check
    db_status = await health_check()
    return {
        "status": "ok",
        "app": "ARIA Podcast Agent",
        "version": "1.0.0",
        "llm_provider": os.getenv("LLM_PROVIDER", "ollama"),
        "llm_model": os.getenv("LLM_MODEL", "gemma3:4b"),
        "databases": db_status,
    }


@app.get("/")
async def root():
    return {
        "message": "🎙️ ARIA Podcast Agent API is running",
        "docs": "/docs",
        "health": "/health",
    }


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "8000")),
        reload=True,
        log_level="info",
    )
