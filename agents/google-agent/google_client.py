"""
Google OAuth 2.0 helpers for the Google agent.

The production SnitchX flow passes per-user access and refresh tokens down from
Firestore. We still keep the in-memory auth store for local/manual debugging so
engineers can authorize the standalone FastAPI server without the full app.
"""

import os
import time
import urllib.parse
from typing import Any, Dict

import httpx

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/contacts.readonly",
]

REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8300/auth/callback")

auth_store: Dict[str, Any] = {
    "access_token": None,
    "refresh_token": None,
    "expires_at": 0,
}


class GoogleAuthRequired(Exception):
    """Raised when no valid Google token exists and user must authenticate."""

    def __init__(self, auth_url: str):
        self.auth_url = auth_url
        super().__init__("Google sign-in required")


def build_auth_url() -> str:
    """Build the Google OAuth consent URL the user must visit."""
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"


def exchange_code_for_tokens(code: str, redirect_uri: str | None = None) -> dict:
    """Exchange the authorization code for access + refresh tokens."""
    effective_redirect_uri = (redirect_uri or REDIRECT_URI).strip()

    response = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": effective_redirect_uri,
        },
    )
    response.raise_for_status()
    data = response.json()

    auth_store["access_token"] = data["access_token"]
    auth_store["refresh_token"] = data.get("refresh_token", auth_store.get("refresh_token"))
    auth_store["expires_at"] = time.time() + data.get("expires_in", 3600)

    return data


def refresh_access_token(refresh_token: str | None = None) -> str:
    """Use a refresh token to get a new access token."""
    using_global_store = refresh_token is None
    resolved_refresh_token = refresh_token or auth_store.get("refresh_token")
    if not resolved_refresh_token:
        raise GoogleAuthRequired(build_auth_url())

    response = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "refresh_token": resolved_refresh_token,
            "grant_type": "refresh_token",
        },
    )

    if response.status_code != 200:
        if using_global_store:
            auth_store["access_token"] = None
            auth_store["refresh_token"] = None
            auth_store["expires_at"] = 0
        raise GoogleAuthRequired(build_auth_url())

    data = response.json()
    if using_global_store:
        auth_store["access_token"] = data["access_token"]
        auth_store["expires_at"] = time.time() + data.get("expires_in", 3600)
    return data["access_token"]


def acquire_google_token(
    access_token: str | None = None,
    refresh_token: str | None = None,
) -> str:
    """
    Resolve a usable Google access token.

    Order of preference:
    1. Access token passed from SnitchX runtime
    2. Refresh token passed from SnitchX runtime
    3. Local debug token store
    """
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise RuntimeError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required in .env")

    if access_token:
        return access_token

    if refresh_token:
        return refresh_access_token(refresh_token)

    token = auth_store.get("access_token")
    expires_at = auth_store.get("expires_at", 0)
    if token and time.time() < (expires_at - 60):
        return token

    if auth_store.get("refresh_token"):
        return refresh_access_token()

    raise GoogleAuthRequired(build_auth_url())
