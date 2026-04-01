from __future__ import annotations

import os
import secrets
import time
from pathlib import Path
from typing import Any

import firebase_admin
from firebase_admin import credentials, firestore

DEFAULT_SERVICE_ACCOUNT_PATH = "/home/ubuntu/app/.secrets/serviceAccountKey.json"
OAUTH_STATE_COLLECTION = "agentOAuthStates"


class OAuthStateError(Exception):
    """Raised when an OAuth state payload is invalid, expired, or already used."""


def _candidate_key_paths() -> list[str]:
    repo_root = Path(__file__).resolve().parents[1]
    candidates = [
        os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY", "").strip(),
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip(),
        DEFAULT_SERVICE_ACCOUNT_PATH,
        str(repo_root / ".secrets" / "serviceAccountKey.json"),
    ]
    return [candidate for candidate in candidates if candidate]


def _ensure_firebase() -> None:
    if firebase_admin._apps:
        return

    for path in _candidate_key_paths():
        if path and os.path.exists(path):
            firebase_admin.initialize_app(credentials.Certificate(path))
            return

    firebase_admin.initialize_app()


def get_db():
    _ensure_firebase()
    return firestore.client()


def user_doc(uid: str):
    return get_db().collection("users").document(uid)


def provider_connection_doc(uid: str, provider: str):
    return user_doc(uid).collection("providerConnections").document(provider)


def get_provider_connection(uid: str, provider: str) -> dict[str, Any] | None:
    snapshot = provider_connection_doc(uid, provider).get()
    if not snapshot.exists:
        return None

    data = snapshot.to_dict() or {}
    if not data.get("accessToken"):
        return None
    return data


def ensure_user_doc(uid: str) -> None:
    user_doc(uid).set({"lastSeenAt": firestore.SERVER_TIMESTAMP}, merge=True)


def save_provider_connection(
    uid: str,
    provider: str,
    *,
    access_token: str,
    refresh_token: str | None = None,
    expires_at: int | None = None,
    scopes: list[str] | None = None,
    metadata: dict[str, str | None] | None = None,
    bundle_id: str | None = None,
    install_targets: list[str] | None = None,
) -> None:
    ensure_user_doc(uid)
    provider_connection_doc(uid, provider).set(
        {
            "provider": provider,
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "expiresAt": expires_at,
            "scopes": scopes or [],
            "metadata": metadata or {},
            "bundleId": bundle_id,
            "connectedAt": firestore.SERVER_TIMESTAMP,
            "updatedAt": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )

    user_updates: dict[str, Any] = {"updatedAt": firestore.SERVER_TIMESTAMP}
    if bundle_id:
        user_updates["connectedBundles"] = firestore.ArrayUnion([bundle_id])
    if install_targets:
        deduped_targets = list(dict.fromkeys(install_targets))
        user_updates["installedAgents"] = firestore.ArrayUnion(deduped_targets)

    user_doc(uid).set(user_updates, merge=True)


def clear_provider_connection(uid: str, provider: str, *, bundle_id: str | None = None) -> None:
    try:
        snapshot = provider_connection_doc(uid, provider).get()
        if snapshot.exists and not bundle_id:
            raw_bundle_id = snapshot.to_dict().get("bundleId")
            if isinstance(raw_bundle_id, str) and raw_bundle_id.strip():
                bundle_id = raw_bundle_id
    except Exception:
        pass

    provider_connection_doc(uid, provider).delete()

    if bundle_id:
        user_doc(uid).set(
            {
                "connectedBundles": firestore.ArrayRemove([bundle_id]),
                "updatedAt": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )


def oauth_state_doc(state_id: str):
    return get_db().collection(OAUTH_STATE_COLLECTION).document(state_id)


def create_oauth_state(payload: dict[str, Any], *, ttl_seconds: int = 900) -> str:
    state_id = secrets.token_urlsafe(24)
    expires_at = int(time.time()) + ttl_seconds
    oauth_state_doc(state_id).set(
        {
            **payload,
            "stateId": state_id,
            "used": False,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "expiresAt": expires_at,
        }
    )
    return state_id


def peek_oauth_state(state_id: str) -> dict[str, Any] | None:
    snapshot = oauth_state_doc(state_id).get()
    if not snapshot.exists:
        return None
    return snapshot.to_dict() or None


@firestore.transactional
def _consume_oauth_state_transaction(transaction, state_ref):
    snapshot = state_ref.get(transaction=transaction)
    if not snapshot.exists:
        raise OAuthStateError("Unknown OAuth state.")

    data = snapshot.to_dict() or {}
    if data.get("used"):
        raise OAuthStateError("OAuth state was already used.")

    expires_at = int(data.get("expiresAt") or 0)
    if expires_at and expires_at < int(time.time()):
        raise OAuthStateError("OAuth state expired.")

    transaction.update(
        state_ref,
        {
            "used": True,
            "usedAt": firestore.SERVER_TIMESTAMP,
        },
    )
    return data


def consume_oauth_state(state_id: str) -> dict[str, Any]:
    transaction = get_db().transaction()
    return _consume_oauth_state_transaction(transaction, oauth_state_doc(state_id))
