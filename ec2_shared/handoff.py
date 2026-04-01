from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

SECRET_ENV_NAMES = ("AGENT_OAUTH_SHARED_SECRET", "AGENT_OAUTH_SECRET")


class HandoffTokenError(Exception):
    """Raised when the signed OAuth handoff token is invalid."""


def _secret() -> str:
    for env_name in SECRET_ENV_NAMES:
        value = os.getenv(env_name, "").strip()
        if value:
            return value
    raise HandoffTokenError(
        "AGENT_OAUTH_SHARED_SECRET is not configured for detached EC2 OAuth handoff."
    )


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _loads(payload_segment: str) -> dict[str, Any]:
    try:
        raw = _b64decode(payload_segment)
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # pragma: no cover - defensive guard
        raise HandoffTokenError("Malformed OAuth handoff token.") from exc

    if not isinstance(data, dict):
        raise HandoffTokenError("Malformed OAuth handoff token payload.")
    return data


def verify_handoff_token(token: str) -> dict[str, Any]:
    if not token or "." not in token:
        raise HandoffTokenError("Missing OAuth handoff token.")

    payload_segment, signature_segment = token.split(".", 1)
    expected_signature = base64.urlsafe_b64encode(
        hmac.new(_secret().encode("utf-8"), payload_segment.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8").rstrip("=")

    if not hmac.compare_digest(expected_signature, signature_segment):
        raise HandoffTokenError("Invalid OAuth handoff signature.")

    payload = _loads(payload_segment)
    expires_at = int(payload.get("expiresAt") or 0)
    if expires_at and expires_at < int(time.time()):
        raise HandoffTokenError("OAuth handoff token expired.")

    return payload
