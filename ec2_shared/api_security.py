from __future__ import annotations

import os
from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _resolve_expected_key() -> str:
    # Support both names to avoid breaking existing deployments.
    return _env("AGENT_API_KEY") or _env("INTERNAL_AGENT_API_KEY")


def _is_public_path(path: str) -> bool:
    if path == "/health":
        return True
    if path.endswith("/health"):
        return True
    return False


def apply_api_security(app: FastAPI) -> None:
    """Attach optional API-key middleware.

    If no API key env var is configured, requests are allowed (backward compatible).
    """

    expected_key = _resolve_expected_key()
    if not expected_key:
        return

    @app.middleware("http")
    async def _api_key_guard(request: Request, call_next: Callable):  # type: ignore[override]
        if _is_public_path(request.url.path):
            return await call_next(request)

        provided_key = (
            request.headers.get("x-agent-api-key")
            or request.headers.get("x-api-key")
            or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        )

        if provided_key != expected_key:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

        return await call_next(request)
