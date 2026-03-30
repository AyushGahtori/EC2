"""
Google OAuth 2.0 — Localhost Redirect Flow (like teams-agent's MSAL pattern).

Instead of the Device Code flow (which requires special GCP project config),
this uses the standard "installed app" redirect to http://localhost:<port>/callback.

Flow:
1. Agent finds no token → raises AuthRequired with a browser URL.
2. User clicks the URL → Google consent screen → redirects to localhost callback.
3. The /auth/callback route on this server catches the code and exchanges it for tokens.
4. Token is stored in memory (auth_store) for subsequent requests.
"""

import os
import time
import urllib.parse
import httpx
from typing import Any, Dict

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

# The redirect URI must match what's registered in Google Cloud Console
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
        "access_type": "offline",   # gets us a refresh_token
        "prompt": "consent",        # always show consent to guarantee refresh_token
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"


def exchange_code_for_tokens(code: str) -> dict:
    """Exchange the authorization code for access + refresh tokens."""
    response = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
        },
    )
    response.raise_for_status()
    data = response.json()

    auth_store["access_token"] = data["access_token"]
    auth_store["refresh_token"] = data.get("refresh_token", auth_store.get("refresh_token"))
    auth_store["expires_at"] = time.time() + data.get("expires_in", 3600)

    return data


def refresh_access_token() -> str:
    """Use the refresh token to get a new access token."""
    rt = auth_store.get("refresh_token")
    if not rt:
        raise GoogleAuthRequired(build_auth_url())

    response = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "refresh_token": rt,
            "grant_type": "refresh_token",
        },
    )

    if response.status_code != 200:
        # Refresh token revoked or expired → force re-auth
        auth_store["access_token"] = None
        auth_store["refresh_token"] = None
        raise GoogleAuthRequired(build_auth_url())

    data = response.json()
    auth_store["access_token"] = data["access_token"]
    auth_store["expires_at"] = time.time() + data.get("expires_in", 3600)
    return data["access_token"]


def acquire_google_token() -> str:
    """
    Get a valid Google access token.
    Raises GoogleAuthRequired with a browser URL if the user hasn't authenticated.
    """
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise RuntimeError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required in .env")

    token = auth_store.get("access_token")
    expires_at = auth_store.get("expires_at", 0)

    # If we have a token and it's not expired (with 60s buffer), use it
    if token and time.time() < (expires_at - 60):
        return token

    # Try refreshing if we have a refresh token
    if auth_store.get("refresh_token"):
        return refresh_access_token()

    # No token at all → user must authenticate
    raise GoogleAuthRequired(build_auth_url())
