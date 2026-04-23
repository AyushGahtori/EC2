from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
except Exception:  # pragma: no cover
    firebase_admin = None  # type: ignore[assignment]
    credentials = firestore = None  # type: ignore[assignment]


UID_PATTERN = re.compile(r"^[A-Za-z0-9._:@-]{3,128}$")
AGENT_COLLECTION = "data-analyst-agent"
CACHE_COLLECTION = "cache"
LOG_COLLECTION = "logs"

FIREBASE_SERVICE_ACCOUNT_KEY = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY", "").strip()
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "").strip() or None

_DB = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


def validated_uid(raw_uid: str | None) -> str | None:
    if raw_uid is None:
        return None
    uid = raw_uid.strip()
    if not uid:
        return None
    if "/" in uid or not UID_PATTERN.match(uid):
        raise ValueError("Invalid userId format for data-analyst-agent storage.")
    return uid


def _firestore_db():
    global _DB
    if _DB is not None:
        return _DB
    if firebase_admin is None or credentials is None or firestore is None or not FIREBASE_SERVICE_ACCOUNT_KEY:
        return None

    if not firebase_admin._apps:
        key_path = Path(FIREBASE_SERVICE_ACCOUNT_KEY)
        if not key_path.is_absolute():
            key_path = Path.cwd() / key_path
        if not key_path.exists():
            return None
        cred = credentials.Certificate(str(key_path))
        options: dict[str, Any] = {}
        if FIREBASE_PROJECT_ID:
            options["projectId"] = FIREBASE_PROJECT_ID
        firebase_admin.initialize_app(
            cred,
            options=options or None,
            name=f"{AGENT_COLLECTION}-admin",
        )

    _DB = firestore.client()
    return _DB


def _cache_doc(uid: str, cache_key: str):
    db = _firestore_db()
    if db is None:
        return None
    return (
        db.collection("users")
        .document(uid)
        .collection("agentReports")
        .document(AGENT_COLLECTION)
        .collection(CACHE_COLLECTION)
        .document(cache_key)
    )


def _log_collection(uid: str):
    db = _firestore_db()
    if db is None:
        return None
    return (
        db.collection("users")
        .document(uid)
        .collection("agentActivity")
        .document(AGENT_COLLECTION)
        .collection(LOG_COLLECTION)
    )


def get_cached_result(uid: str | None, cache_key: str, max_age_seconds: int) -> tuple[dict[str, Any] | None, str | None]:
    if not uid:
        return None, None
    ref = _cache_doc(uid, cache_key)
    if ref is None:
        return None, None

    snapshot = ref.get()
    if not snapshot.exists:
        return None, None
    data = snapshot.to_dict() or {}
    payload = data.get("payload")
    updated_at_iso = str(data.get("updatedAtIso") or data.get("createdAtIso") or "").strip()
    if not isinstance(payload, dict) or not updated_at_iso:
        return None, None
    try:
        updated_at = datetime.fromisoformat(updated_at_iso)
    except ValueError:
        return None, None

    if _utc_now() - updated_at > timedelta(seconds=max_age_seconds):
        return None, None
    return payload, "firestore"


def save_cached_result(uid: str | None, cache_key: str, payload: dict[str, Any]) -> None:
    if not uid:
        return
    ref = _cache_doc(uid, cache_key)
    if ref is None:
        return
    now_iso = _iso_now()
    ref.set(
        {
            "uid": uid,
            "agentId": AGENT_COLLECTION,
            "cacheKey": cache_key,
            "payload": payload,
            "updatedAtIso": now_iso,
            "createdAtIso": now_iso,
        },
        merge=True,
    )


def save_analysis_log(uid: str | None, payload: dict[str, Any], status: str) -> None:
    if not uid:
        return
    collection = _log_collection(uid)
    if collection is None:
        return
    doc = {
        "uid": uid,
        "agentId": AGENT_COLLECTION,
        "status": status,
        "payload": payload,
        "createdAtIso": _iso_now(),
    }
    collection.document().set(doc, merge=True)
