"""
Marketing AI Agent — FastAPI Application Entry Point
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routes import chat, config as config_routes, sessions, upload, products
from config.settings import get_settings
from models.schemas import HealthResponse
from services.mongodb_service import health_check as mongo_health
from services.redis_service import health_check as redis_health

# ── Logging ───────────────────────────────────────────────────────────────────

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ]
)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Startup / Shutdown ────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # Ensure upload directory exists
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    logger.info(f"Upload directory: {settings.upload_dir}")

    # Warm up LLM (initialise the model client). If the .env-chosen provider
    # has no key, runtime_config falls back to one that does and we log a warning.
    try:
        from services.llm_service import get_llm
        from services.runtime_config import active_model_for, resolve_boot_provider

        active, warning = resolve_boot_provider()
        if warning:
            logger.warning(warning)
        get_llm()
        logger.info(f"LLM ready: provider={active} model={active_model_for(active)}")
    except Exception as e:
        logger.warning(f"LLM warm-up failed (will retry on first request): {e}")

    # Warm up agent graph
    try:
        from agents.marketing_agent import get_agent_graph
        get_agent_graph()
        logger.info("LangGraph agent compiled and ready")
    except Exception as e:
        logger.warning(f"Agent graph warm-up failed: {e}")

    from services.runtime_config import active_model_for, get_active_provider
    active = get_active_provider()
    logger.info(f"🚀 Marketing AI Agent running on {settings.api_host}:{settings.api_port}")
    logger.info(f"   Provider: {active.upper()} | Model: {active_model_for(active)}")

    yield

    logger.info("Shutting down Marketing AI Agent")


# ── App ───────────────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Marketing AI Agent API",
        description="Autonomous marketing agent powered by LangGraph ReAct + LLM",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routes
    app.include_router(sessions.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(upload.router, prefix="/api")
    app.include_router(products.router, prefix="/api")
    app.include_router(config_routes.router, prefix="/api")

    # Static file serving for uploads
    upload_path = Path(settings.upload_dir)
    upload_path.mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=str(upload_path)), name="uploads")

    @app.get("/", tags=["root"])
    async def root():
        return {
            "message": "Marketing AI Agent API",
            "docs": "/docs",
            "status": "running",
        }

    @app.get("/health", response_model=HealthResponse, tags=["health"])
    async def health():
        from services.runtime_config import active_model_for, get_active_provider
        redis_ok = await redis_health()
        mongo_ok = await mongo_health()
        provider = get_active_provider()

        return HealthResponse(
            status="healthy" if redis_ok and mongo_ok else "degraded",
            provider=provider,
            model=active_model_for(provider),
            redis=redis_ok,
            mongodb=mongo_ok,
        )

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run(
        "main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
        log_level="info",
    )
