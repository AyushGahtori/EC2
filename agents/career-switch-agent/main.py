"""
main.py — FastAPI application entry point.

Wires together:
- App lifecycle (startup / shutdown)
- CORS middleware
- Request logging middleware
- API router
- OpenAPI docs customization
"""

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from config import get_settings
from database import close_database, ensure_indexes, ping_database
from routes import router

# ─────────────────────────────────────────────
# Logging setup
# ─────────────────────────────────────────────
settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Lifespan — startup & shutdown
# ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── STARTUP ──
    logger.info("🚀 Career Transition Agent API starting up...")

    if ping_database():
        logger.info("✅ Firestore connected.")
        ensure_indexes()
    else:
        logger.critical("❌ Firestore not reachable — check FIREBASE_SERVICE_ACCOUNT_KEY and Firestore credentials.")

    yield  # App runs here

    # ── SHUTDOWN ──
    logger.info("🛑 Shutting down...")
    close_database()


# ─────────────────────────────────────────────
# App factory
# ─────────────────────────────────────────────
app = FastAPI(
    title="AI Career Transition Agent API",
    description=(
        "A production-grade backend that computes skill gaps using Firestore O*NET data, "
        "fetches live job market signals from JSearch and Adzuna, and uses Google Gemini to generate "
        "a structured, validated career transition plan."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


# ─────────────────────────────────────────────
# CORS Middleware
# ─────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # Tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────
# Request Logging Middleware
# ─────────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    req_id = str(uuid.uuid4())[:8]
    start = time.perf_counter()
    logger.info(f"[{req_id}] ▶  {request.method} {request.url.path}")

    try:
        response = await call_next(request)
    except Exception as exc:
        logger.exception(f"[{req_id}] ❌ Unhandled exception: {exc}")
        return JSONResponse(status_code=500, content={"success": False, "error": "Internal server error."})

    elapsed = (time.perf_counter() - start) * 1000
    logger.info(f"[{req_id}] ◀  {response.status_code} ({elapsed:.1f}ms)")
    return response


# ─────────────────────────────────────────────
# Register Router
# ─────────────────────────────────────────────
app.include_router(router, prefix="/api")

frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/app", StaticFiles(directory=frontend_dir, html=True), name="frontend")


# ─────────────────────────────────────────────
# Root endpoint
# ─────────────────────────────────────────────
@app.get("/", tags=["Root"])
async def root():
    return {
        "service": "AI Career Transition Agent",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/api/health",
    }


# ─────────────────────────────────────────────
# Entry point — run with: python main.py
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,          # auto-restart on file changes
        log_level=settings.LOG_LEVEL.lower(),
    )
