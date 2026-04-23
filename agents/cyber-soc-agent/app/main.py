"""
Cyber AI SOC — Main application entry point.
Registers all routers; serves the Bootstrap 5 frontend.
"""
import logging
import os

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routes.analysis import router as analysis_router
from app.routes.logs import router as logs_router

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

settings = get_settings()

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title=settings.app_name,
    description="AI-powered cybersecurity threat analysis with Windows Event Log integration",
    version="2.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(analysis_router)
app.include_router(logs_router)


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


# ── Static frontend ───────────────────────────────────────────────────────────
frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
frontend_dir = os.path.abspath(frontend_dir)

if os.path.exists(frontend_dir):
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

    @app.get("/")
    async def root():
        return FileResponse(os.path.join(frontend_dir, "index.html"))

    logger.info("Frontend mounted from %s", frontend_dir)
else:
    logger.warning("Frontend directory not found: %s", frontend_dir)

logger.info("Cyber AI SOC v2.0 started | VT=%s | LLM=%s@%s",
            bool(settings.virustotal_api_key),
            settings.ollama_model,
            settings.ollama_base_url)
