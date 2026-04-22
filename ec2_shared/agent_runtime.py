from __future__ import annotations

import os
from typing import Any

from ec2_shared.firestore_store import get_provider_connection


def get_public_base_url() -> str:
    return os.getenv("AGENT_PUBLIC_BASE_URL", "").strip().rstrip("/")


def build_agent_auth_url(agent_slug: str) -> str:
    base_url = get_public_base_url()
    if not base_url:
        # Safe fallback: caller can resolve this relative URL against its runtime base.
        return f"/{agent_slug}/auth/login"
    return f"{base_url}/{agent_slug}/auth/login"


def auth_required_response(
    *,
    agent_slug: str,
    agent_id: str,
    provider: str,
    message: str,
    bundle_id: str | None = None,
) -> dict[str, Any]:
    return {
        "status": "action_required",
        "type": "oauth_required",
        "message": message,
        "error": "AUTH_REQUIRED",
        "provider": provider,
        "auth_url": build_agent_auth_url(agent_slug),
        "agentId": agent_id,
        "bundleId": bundle_id,
    }


def resolve_provider_credentials(
    *,
    user_id: str | None,
    provider: str,
    access_token: str | None = None,
    refresh_token: str | None = None,
) -> dict[str, Any]:
    resolved_access_token = access_token or None
    resolved_refresh_token = refresh_token or None
    metadata: dict[str, Any] = {}

    if user_id and not resolved_access_token:
        connection = get_provider_connection(user_id, provider)
        if connection:
            resolved_access_token = resolved_access_token or connection.get("accessToken")
            resolved_refresh_token = resolved_refresh_token or connection.get("refreshToken")
            raw_metadata = connection.get("metadata")
            if isinstance(raw_metadata, dict):
                metadata = raw_metadata

    payload: dict[str, Any] = {
        "access_token": resolved_access_token,
        "refresh_token": resolved_refresh_token,
    }
    payload.update(metadata)
    return payload
