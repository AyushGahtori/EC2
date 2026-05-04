"""
webhook.py - FastAPI webhook server

Receives GitHub PR webhook events and triggers the LangGraph pipeline.
Validates HMAC-SHA256 signatures, handles opened/synchronize events.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os

from fastapi import FastAPI, Header, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse

from pr_copilot.config import settings
from pr_copilot.graph import compiled_graph
from pr_copilot.state import PRCopilotState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="PR Copilot",
    description="AI-powered Pull Request reviewer powered by LangGraph + qwen3-coder:480b-cloud",
    version="1.0.0",
)


# ── Signature verification ─────────────────────────────────────────────────────

def verify_github_signature(payload: bytes, signature_header: str) -> bool:
    """Verify X-Hub-Signature-256 HMAC."""
    if not settings.webhook_secret:
        logger.warning("WEBHOOK_SECRET not set — skipping signature check.")
        return True
    if not signature_header:
        return False
    expected = "sha256=" + hmac.new(
        settings.webhook_secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# ── Background pipeline runner ────────────────────────────────────────────────

async def run_pipeline(repo: str, pr_number: int) -> None:
    """Execute the full LangGraph pipeline for a PR."""
    logger.info("Starting pipeline for %s PR #%s", repo, pr_number)
    initial_state = PRCopilotState(repo=repo, pr_number=pr_number)
    try:
        result = compiled_graph.invoke(initial_state)
        final_state = (
            result
            if isinstance(result, PRCopilotState)
            else PRCopilotState.model_validate(result)
        )
        if final_state.error:
            logger.error("Pipeline finished with error: %s", final_state.error)
        else:
            logger.info(
                "Pipeline complete — %s | %d comments posted",
                final_state.review_summary,
                len(final_state.final_comments),
            )
    except Exception:
        logger.exception("Unhandled exception in pipeline for %s PR #%s", repo, pr_number)


# ── Routes ──────────────────────────────────────────────────────────────────

@app.post("/webhook/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str = Header(default=""),
):
    """
    GitHub webhook endpoint.

    Supported events: pull_request (opened, synchronize, reopened)
    """
    body = await request.body()

    # Verify signature
    if not verify_github_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = await request.json()

    # Only handle pull_request events
    if x_github_event != "pull_request":
        return JSONResponse({"status": "ignored", "event": x_github_event})

    action = payload.get("action", "")
    if action not in {"opened", "synchronize", "reopened"}:
        return JSONResponse({"status": "ignored", "action": action})

    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {}).get("full_name", "")
    pr_number = pr.get("number", 0)

    if not repo or not pr_number:
        raise HTTPException(status_code=400, detail="Missing repo or PR number")

    logger.info("Received PR event: %s #%s action=%s", repo, pr_number, action)

    # Run pipeline in background so webhook returns immediately
    background_tasks.add_task(run_pipeline, repo, pr_number)

    return JSONResponse({
        "status": "accepted",
        "repo": repo,
        "pr_number": pr_number,
        "action": action,
    })


@app.get("/health")
async def health():
    return {"status": "ok", "model": settings.ollama_model}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "pr_copilot.webhook:app",
        host="0.0.0.0",
        port=settings.webhook_port,
        reload=False,
    )
