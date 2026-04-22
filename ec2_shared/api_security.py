from __future__ import annotations

import os
from typing import Iterable
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _normalize_origin(value: str) -> str | None:
    candidate = (value or "").strip().rstrip("/")
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _split_csv(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _default_cors_origins() -> list[str]:
    candidates: list[str] = []
    candidates.extend(_split_csv("WEB_ORIGIN"))
    candidates.extend(_split_csv("WEB_BASE_URL"))
    candidates.extend(_split_csv("NEXT_PUBLIC_APP_URL"))
    candidates.extend(_split_csv("AGENT_WEB_BASE_URL"))
    candidates.extend(_split_csv("AGENT_ALLOWED_ORIGINS"))

    vercel_url = os.getenv("VERCEL_URL", "").strip()
    if vercel_url:
        candidate = vercel_url if vercel_url.startswith("http") else f"https://{vercel_url}"
        candidates.append(candidate)

    if _env_bool("ALLOW_LOCALHOST_ORIGINS", True):
        candidates.extend(
            [
                "http://localhost:3000",
                "http://127.0.0.1:3000",
                "http://localhost:5173",
                "http://127.0.0.1:5173",
            ]
        )

    resolved: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        normalized = _normalize_origin(item)
        if normalized and normalized not in seen:
            seen.add(normalized)
            resolved.append(normalized)
    return resolved


def resolve_cors_origins() -> list[str]:
    if _env_bool("CORS_ALLOW_ALL_ORIGINS", False):
        return ["*"]

    configured = _split_csv("CORS_ORIGINS")
    resolved: list[str] = []
    seen: set[str] = set()
    for item in configured:
        normalized = _normalize_origin(item)
        if normalized and normalized not in seen:
            seen.add(normalized)
            resolved.append(normalized)

    if resolved:
        return resolved
    return _default_cors_origins()


def _normalize_host(value: str) -> str | None:
    candidate = (value or "").strip()
    if not candidate:
        return None
    if "://" in candidate:
        parsed = urlparse(candidate)
        candidate = parsed.netloc
    return candidate or None


def _iter_default_hosts() -> Iterable[str]:
    for env_name in ("AGENT_PUBLIC_BASE_URL", "WEB_BASE_URL", "NEXT_PUBLIC_APP_URL"):
        raw = os.getenv(env_name, "")
        normalized = _normalize_host(raw)
        if normalized:
            yield normalized


def resolve_trusted_hosts() -> list[str]:
    configured = _split_csv("TRUSTED_HOSTS")
    if any(item.strip() == "*" for item in configured):
        return ["*"]

    resolved: list[str] = []
    seen: set[str] = set()

    source = configured if configured else list(_iter_default_hosts())
    if not source:
        # Backward-compatible default: do not block traffic when no explicit
        # host policy exists yet.
        return ["*"]

    source.extend(["localhost", "127.0.0.1", "[::1]"])
    for item in source:
        normalized = _normalize_host(item)
        if normalized and normalized not in seen:
            seen.add(normalized)
            resolved.append(normalized)

    return resolved or ["*"]


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        response.headers.setdefault("X-XSS-Protection", "0")
        return response


def apply_api_security(app: FastAPI, *, default_allow_credentials: bool = False) -> None:
    cors_origins = resolve_cors_origins()
    allow_credentials = _env_bool("CORS_ALLOW_CREDENTIALS", default_allow_credentials)

    # Browsers reject wildcard origins with credentials. Keep this safe by default.
    if cors_origins == ["*"] and allow_credentials:
        allow_credentials = False

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    trusted_hosts = resolve_trusted_hosts()
    if trusted_hosts != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)

    app.add_middleware(SecurityHeadersMiddleware)
