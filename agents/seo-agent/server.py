from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from schemas import SEOActionRequest, SEOActionResponse
from seo_service import run_seo_analysis

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="SEO Agent API",
    description="SEO content brief, audit, and rewrite guidance agent.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "healthy",
        "agent": "seo-agent",
        "displayName": "SEO Agent",
        "version": "1.0.0",
    }


@app.get("/seo-agent/health")
@app.get("/seo/health")
def seo_health() -> dict[str, str]:
    return health()


@app.post("/seo-agent/action", response_model=SEOActionResponse)
@app.post("/seo/action", response_model=SEOActionResponse)
async def seo_action(req: SEOActionRequest) -> SEOActionResponse:
    try:
        return await run_seo_analysis(req)
    except ValueError as exc:
        logger.info("SEO agent validation failure: %s", exc)
        return SEOActionResponse(
            status="failed",
            type="seo_result",
            displayName="SEO Agent",
            message="SEO analysis could not be completed with the provided input.",
            error=str(exc),
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("SEO agent execution failed")
        return SEOActionResponse(
            status="failed",
            type="seo_result",
            displayName="SEO Agent",
            message="The SEO agent ran into a problem while generating the analysis.",
            error="SEO analysis failed.",
        )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8034"))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
