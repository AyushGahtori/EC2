"""
Lead Generation AI Agent — FastAPI Application Entry Point
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config.settings import settings

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# FastAPI App
# ─────────────────────────────────────────────
app = FastAPI(
    title="Lead Generation AI Agent",
    description="Autonomous AI agent for lead generation using LangGraph + ReAct",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ─────────────────────────────────────────────
# CORS
# ─────────────────────────────────────────────
origins = [o.strip() for o in settings.CORS_ORIGINS.split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────
app.include_router(router, prefix="/api")


@app.get("/")
async def root():
    return {
        "service": "Lead Generation AI Agent",
        "version": "1.0.0",
        "provider": settings.MODEL_PROVIDER,
        "model": settings.MODEL_NAME,
        "docs": "/docs",
    }


# ─────────────────────────────────────────────
# Startup / Shutdown
# ─────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    logger.info(f"Starting LeadGen Agent — provider={settings.MODEL_PROVIDER} model={settings.MODEL_NAME}")
    logger.info(f"MongoDB: {settings.MONGODB_URI}")
    logger.info(f"Redis: {settings.REDIS_URL}")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down LeadGen Agent")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=True,
        log_level="info",
    )
