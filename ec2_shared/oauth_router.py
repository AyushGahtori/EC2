from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlparse

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from ec2_shared.firestore_store import (
    OAuthStateError,
    clear_provider_connection,
    consume_oauth_state,
    create_oauth_state,
    get_provider_connection,
    peek_oauth_state,
    save_provider_connection,
)
from ec2_shared.handoff import HandoffTokenError, verify_handoff_token


@dataclass
class OAuthAgentRegistration:
    provider: str
    agent_slug: str
    display_name: str
    default_bundle_id: str | None = None
    default_scopes: list[str] = field(default_factory=list)


class LogoutRequest(BaseModel):
    handoff: str


def _forwarded_origin(request: Request) -> str:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    ).split(",")[0].strip()
    return f"{proto}://{host}"


def _callback_url(request: Request, agent_slug: str) -> str:
    public_base = _get_env("AGENT_PUBLIC_BASE_URL")
    base = public_base.rstrip("/") if public_base else _forwarded_origin(request)
    return f"{base}/{agent_slug}/auth/callback"


def _is_localhost_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").strip().lower()
    except ValueError:
        return False
    return host in {"localhost", "127.0.0.1", "::1"}


def _effective_redirect_uri(provider: str, request: Request, agent_slug: str) -> str:
    overrides = {
        "google": _get_env("GOOGLE_REDIRECT_URI"),
        "microsoft": _get_env("MICROSOFT_REDIRECT_URI"),
        "notion": _get_env("NOTION_REDIRECT_URI"),
        "github": _get_env("GITHUB_REDIRECT_URI"),
        "gitlab": _get_env("GITLAB_REDIRECT_URI"),
        "discord": _get_env("DISCORD_REDIRECT_URI"),
        "dropbox": _get_env("DROPBOX_REDIRECT_URI"),
        "atlassian": _get_env("JIRA_REDIRECT_URI"),
        "linkedin": _get_env("LINKEDIN_REDIRECT_URI"),
        "zoom": _get_env("ZOOM_REDIRECT_URI"),
        "canva": _get_env("CANVA_REDIRECT_URI"),
    }
    override = overrides.get(provider, "")
    if override:
        # Safety valve: detached EC2 runtimes often keep AGENT_PUBLIC_BASE_URL set.
        # If an old localhost redirect override is left behind, prefer the public callback.
        if _is_localhost_url(override) and _get_env("AGENT_PUBLIC_BASE_URL"):
            return _callback_url(request, agent_slug)
        return override

    return _callback_url(request, agent_slug)


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(72)
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    return verifier, challenge


def _get_env(name: str, fallback: str = "") -> str:
    return os.getenv(name, fallback).strip()


def _client_id(provider: str) -> str:
    mapping = {
        "google": _get_env("GOOGLE_CLIENT_ID"),
        "microsoft": _get_env("MICROSOFT_CLIENT_ID") or _get_env("GRAPH_CLIENT_ID"),
        "notion": _get_env("NOTION_CLIENT_ID"),
        "github": _get_env("GITHUB_CLIENT_ID"),
        "gitlab": _get_env("GITLAB_CLIENT_ID"),
        "discord": _get_env("DISCORD_CLIENT_ID"),
        "dropbox": _get_env("DROPBOX_CLIENT_ID"),
        "atlassian": _get_env("JIRA_CLIENT_ID"),
        "linkedin": _get_env("LINKEDIN_CLIENT_ID"),
        "zoom": _get_env("ZOOM_CLIENT_ID"),
        "canva": _get_env("CANVA_CLIENT_ID"),
    }
    return mapping.get(provider, "")


def _client_secret(provider: str) -> str:
    mapping = {
        "google": _get_env("GOOGLE_CLIENT_SECRET"),
        "microsoft": _get_env("MICROSOFT_CLIENT_SECRET"),
        "notion": _get_env("NOTION_CLIENT_SECRET"),
        "github": _get_env("GITHUB_CLIENT_SECRET"),
        "gitlab": _get_env("GITLAB_CLIENT_SECRET"),
        "discord": _get_env("DISCORD_CLIENT_SECRET"),
        "dropbox": _get_env("DROPBOX_CLIENT_SECRET"),
        "atlassian": _get_env("JIRA_CLIENT_SECRET"),
        "linkedin": _get_env("LINKEDIN_CLIENT_SECRET"),
        "zoom": _get_env("ZOOM_CLIENT_SECRET"),
        "canva": _get_env("CANVA_CLIENT_SECRET"),
    }
    return mapping.get(provider, "")


def _build_auth_url(
    *,
    provider: str,
    request: Request,
    agent_slug: str,
    scopes: list[str],
    state_id: str,
    pkce_challenge: str | None = None,
) -> str:
    redirect_uri = _effective_redirect_uri(provider, request, agent_slug)
    client_id = _client_id(provider)
    if not client_id:
        raise HTTPException(status_code=500, detail=f"OAuth client ID is missing for {provider}.")

    if provider == "google":
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state_id,
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

    if provider == "microsoft":
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state_id,
            "prompt": "consent",
        }
        if pkce_challenge:
            params["code_challenge"] = pkce_challenge
            params["code_challenge_method"] = "S256"
        tenant = _get_env("GRAPH_TENANT_ID", "common") or "common"
        return (
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?{urlencode(params)}"
        )

    if provider == "notion":
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "owner": "user",
            "state": state_id,
        }
        return f"https://api.notion.com/v1/oauth/authorize?{urlencode(params)}"

    if provider == "github":
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state_id,
        }
        return f"https://github.com/login/oauth/authorize?{urlencode(params)}"

    if provider == "gitlab":
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state_id,
        }
        return f"https://gitlab.com/oauth/authorize?{urlencode(params)}"

    if provider == "discord":
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "prompt": "consent",
            "state": state_id,
        }
        return f"https://discord.com/api/oauth2/authorize?{urlencode(params)}"

    if provider == "dropbox":
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "token_access_type": "offline",
            "state": state_id,
        }
        return f"https://www.dropbox.com/oauth2/authorize?{urlencode(params)}"

    if provider == "atlassian":
        params = {
            "audience": "api.atlassian.com",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "prompt": "consent",
            "scope": " ".join(scopes),
            "state": state_id,
        }
        return f"https://auth.atlassian.com/authorize?{urlencode(params)}"

    if provider == "linkedin":
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state_id,
            "scope": " ".join(scopes),
        }
        return f"https://www.linkedin.com/oauth/v2/authorization?{urlencode(params)}"

    if provider == "zoom":
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state_id,
        }
        return f"https://zoom.us/oauth/authorize?{urlencode(params)}"

    if provider == "canva":
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state_id,
            "code_challenge": pkce_challenge or "",
            "code_challenge_method": "S256",
        }
        return f"https://www.canva.com/api/oauth/authorize?{urlencode(params)}"

    raise HTTPException(status_code=400, detail=f"Unsupported OAuth provider: {provider}")


def _exchange_code(
    *,
    provider: str,
    code: str,
    request: Request,
    agent_slug: str,
    scopes: list[str],
    pkce_verifier: str | None = None,
) -> dict[str, Any]:
    redirect_uri = _effective_redirect_uri(provider, request, agent_slug)
    client_id = _client_id(provider)
    client_secret = _client_secret(provider)

    if provider == "google":
        response = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    if provider == "microsoft":
        tenant = _get_env("GRAPH_TENANT_ID", "common") or "common"
        body = {
            "client_id": client_id,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
        }
        if client_secret:
            body["client_secret"] = client_secret
        if pkce_verifier:
            body["code_verifier"] = pkce_verifier
        response = requests.post(
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            data=body,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    if provider == "notion":
        basic_auth = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("utf-8")
        response = requests.post(
            "https://api.notion.com/v1/oauth/token",
            headers={
                "Authorization": f"Basic {basic_auth}",
                "Content-Type": "application/json",
            },
            json={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    if provider == "github":
        response = requests.post(
            "https://github.com/login/oauth/access_token",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    if provider == "gitlab":
        response = requests.post(
            "https://gitlab.com/oauth/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    if provider == "discord":
        response = requests.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    if provider == "dropbox":
        response = requests.post(
            "https://api.dropboxapi.com/oauth2/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    if provider == "atlassian":
        response = requests.post(
            "https://auth.atlassian.com/oauth/token",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    if provider == "linkedin":
        response = requests.post(
            "https://www.linkedin.com/oauth/v2/accessToken",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    if provider == "zoom":
        basic_auth = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("utf-8")
        response = requests.post(
            "https://zoom.us/oauth/token",
            headers={
                "Authorization": f"Basic {basic_auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    if provider == "canva":
        basic_auth = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("utf-8")
        response = requests.post(
            "https://api.canva.com/rest/v1/oauth/token",
            headers={
                "Authorization": f"Basic {basic_auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": pkce_verifier or "",
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    raise HTTPException(status_code=400, detail=f"Unsupported OAuth provider: {provider}")


def _provider_metadata(provider: str, token_data: dict[str, Any]) -> dict[str, str | None]:
    if provider != "linkedin":
        return {}

    access_token = token_data.get("access_token", "")
    if not access_token:
        return {}

    try:
        response = requests.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        if response.ok:
            payload = response.json()
            sub = payload.get("sub")
            if sub:
                return {"urn": f"urn:li:person:{sub}"}
    except Exception:
        return {}
    return {}


def _html_result(
    *,
    success: bool,
    message: str,
    return_origin: str | None,
    bundle_id: str | None = None,
    agent_id: str | None = None,
    provider: str | None = None,
) -> HTMLResponse:
    payload = {
        "type": "snitchx_oauth_success" if success else "snitchx_oauth_error",
        "bundleId": bundle_id,
        "agentId": agent_id,
        "provider": provider,
        "message": message,
    }
    color = "#16a34a" if success else "#ef4444"
    heading = "Connection complete" if success else "Connection failed"
    target_origin = return_origin or "*"

    return HTMLResponse(
        f"""<html><body style="background:#0a0a0a;color:#f5f5f5;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
            <div style="max-width:420px;text-align:center;padding:24px;border:1px solid rgba(255,255,255,0.12);border-radius:18px;background:rgba(255,255,255,0.03)">
                <h2 style="margin:0 0 12px;color:{color}">{heading}</h2>
                <p style="margin:0 0 12px;color:#d4d4d8">{message}</p>
                <p style="margin:0;color:#71717a">This window will close automatically.</p>
            </div>
            <script>
                (function () {{
                    try {{
                        if (window.opener && !window.opener.closed) {{
                            window.opener.postMessage({payload}, "{target_origin}");
                        }}
                    }} catch (_) {{}}
                    setTimeout(function () {{ window.close(); }}, 1200);
                }})();
            </script>
        </body></html>""",
        status_code=200 if success else 400,
    )


def register_oauth_routes(app: FastAPI, registration: OAuthAgentRegistration) -> None:
    provider = registration.provider
    agent_slug = registration.agent_slug

    @app.get("/auth/login")
    def auth_login(request: Request, handoff: str = ""):
        if not handoff:
            raise HTTPException(
                status_code=400,
                detail="Missing signed OAuth handoff token. Start authorization from the web app.",
            )

        try:
            payload = verify_handoff_token(handoff)
        except HandoffTokenError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if payload.get("provider") != provider:
            raise HTTPException(status_code=400, detail="OAuth handoff provider mismatch.")

        scopes = payload.get("scopes")
        if not isinstance(scopes, list):
            scopes = list(registration.default_scopes)

        pkce_verifier = None
        pkce_challenge = None
        if provider in {"canva", "microsoft"}:
            pkce_verifier, pkce_challenge = _pkce_pair()

        state_id = create_oauth_state(
            {
                **payload,
                "provider": provider,
                "agentSlug": agent_slug,
                "pkceVerifier": pkce_verifier,
            }
        )
        auth_url = _build_auth_url(
            provider=provider,
            request=request,
            agent_slug=agent_slug,
            scopes=scopes,
            state_id=state_id,
            pkce_challenge=pkce_challenge,
        )
        return RedirectResponse(auth_url, status_code=302)

    @app.get("/auth/callback")
    def auth_callback(
        request: Request,
        code: str = "",
        state: str = "",
        error: str = "",
    ):
        peeked_state = peek_oauth_state(state) if state else None
        return_origin = peeked_state.get("returnOrigin") if peeked_state else None
        bundle_id = peeked_state.get("bundleId") if peeked_state else None
        agent_id = peeked_state.get("agentId") if peeked_state else None

        if error:
            return _html_result(
                success=False,
                message=error,
                return_origin=return_origin,
                bundle_id=bundle_id,
                agent_id=agent_id,
                provider=provider,
            )

        if not code or not state:
            return _html_result(
                success=False,
                message="Missing OAuth callback parameters.",
                return_origin=return_origin,
                bundle_id=bundle_id,
                agent_id=agent_id,
                provider=provider,
            )

        try:
            state_payload = consume_oauth_state(state)
            scopes = state_payload.get("scopes")
            if not isinstance(scopes, list):
                scopes = list(registration.default_scopes)

            token_data = _exchange_code(
                provider=provider,
                code=code,
                request=request,
                agent_slug=agent_slug,
                scopes=scopes,
                pkce_verifier=state_payload.get("pkceVerifier"),
            )
            metadata = _provider_metadata(provider, token_data)
            expires_in = token_data.get("expires_in")
            expires_at = None
            if expires_in is not None:
                try:
                    expires_at = int(time.time()) + int(expires_in)
                except (TypeError, ValueError):
                    expires_at = None

            save_provider_connection(
                state_payload["uid"],
                provider,
                access_token=token_data["access_token"],
                refresh_token=token_data.get("refresh_token"),
                expires_at=expires_at,
                scopes=scopes,
                metadata=metadata,
                bundle_id=state_payload.get("bundleId") or registration.default_bundle_id,
                install_targets=state_payload.get("installTargets"),
            )

            label = state_payload.get("displayName") or registration.display_name
            return _html_result(
                success=True,
                message=f"{label} connected successfully.",
                return_origin=state_payload.get("returnOrigin"),
                bundle_id=state_payload.get("bundleId"),
                agent_id=state_payload.get("agentId"),
                provider=provider,
            )
        except OAuthStateError as exc:
            return _html_result(
                success=False,
                message=str(exc),
                return_origin=return_origin,
                bundle_id=bundle_id,
                agent_id=agent_id,
                provider=provider,
            )
        except requests.HTTPError as exc:
            error_message = exc.response.text if exc.response is not None else str(exc)
            return _html_result(
                success=False,
                message=error_message,
                return_origin=return_origin,
                bundle_id=bundle_id,
                agent_id=agent_id,
                provider=provider,
            )
        except Exception as exc:  # pragma: no cover - defensive error surface
            return _html_result(
                success=False,
                message=str(exc),
                return_origin=return_origin,
                bundle_id=bundle_id,
                agent_id=agent_id,
                provider=provider,
            )

    @app.get("/auth/status")
    def auth_status(handoff: str = ""):
        if not handoff:
            raise HTTPException(status_code=400, detail="Missing signed OAuth handoff token.")

        try:
            payload = verify_handoff_token(handoff)
        except HandoffTokenError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if payload.get("provider") != provider:
            raise HTTPException(status_code=400, detail="OAuth handoff provider mismatch.")

        connection = get_provider_connection(payload["uid"], provider)
        return {"authenticated": bool(connection and connection.get("accessToken"))}

    @app.post("/auth/logout")
    def auth_logout(body: LogoutRequest):
        try:
            payload = verify_handoff_token(body.handoff)
        except HandoffTokenError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if payload.get("provider") != provider:
            raise HTTPException(status_code=400, detail="OAuth handoff provider mismatch.")

        clear_provider_connection(
            payload["uid"],
            provider,
            bundle_id=(payload.get("bundleId") or registration.default_bundle_id),
        )
        return {"status": "logged_out"}
