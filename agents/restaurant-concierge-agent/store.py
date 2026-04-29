from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
except Exception:  # pragma: no cover
    firebase_admin = None
    credentials = None
    firestore = None


UID_PATTERN = re.compile(r"^[A-Za-z0-9._:@-]{3,128}$")
AGENT_COLLECTION = "restaurant-concierge-agent"
BASE_DIR = Path(__file__).resolve().parent
LOCAL_DATA_DIR = BASE_DIR / "runtime_data"
LOCAL_DATA_PATH = LOCAL_DATA_DIR / "store.json"
LOCK = threading.Lock()
DB = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso() -> str:
    return _utc_now().isoformat()


def validated_uid(raw_uid: str | None) -> str | None:
    if raw_uid is None:
        return None
    uid = raw_uid.strip()
    if not uid:
        return None
    if "/" in uid or not UID_PATTERN.match(uid):
        raise ValueError("Invalid userId format for restaurant-concierge-agent storage.")
    return uid


def _resolve_key_path() -> Path | None:
    env_path = (os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    if env_path:
        candidate = Path(env_path)
        if not candidate.is_absolute():
            candidate = BASE_DIR / env_path
        if candidate.exists():
            return candidate

    local_path = BASE_DIR / "serviceAccountKey.json"
    if local_path.exists():
        return local_path

    ec2_path = Path("/home/ubuntu/app/.secrets/serviceAccountKey.json")
    if ec2_path.exists():
        return ec2_path
    return None


def _firestore_db():
    global DB
    if DB is not None:
        return DB
    if firebase_admin is None or credentials is None or firestore is None:
        return None

    try:
        if not firebase_admin._apps:
            key_path = _resolve_key_path()
            if key_path is not None:
                firebase_admin.initialize_app(
                    credentials.Certificate(str(key_path)),
                    name=f"{AGENT_COLLECTION}-admin",
                )
            else:
                firebase_admin.initialize_app(name=f"{AGENT_COLLECTION}-admin")
        DB = firestore.client()
        return DB
    except Exception:
        return None


def _ensure_local_store() -> None:
    LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not LOCAL_DATA_PATH.exists():
        LOCAL_DATA_PATH.write_text(
            json.dumps({"sessions": {}, "logs": {}}, indent=2),
            encoding="utf-8",
        )


def _read_local_store() -> dict[str, Any]:
    _ensure_local_store()
    with LOCK:
        try:
            return json.loads(LOCAL_DATA_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {"sessions": {}, "logs": {}}


def _write_local_store(payload: dict[str, Any]) -> None:
    _ensure_local_store()
    with LOCK:
        LOCAL_DATA_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _session_doc(uid: str, session_key: str):
    db = _firestore_db()
    if db is None:
        return None
    return (
        db.collection("users")
        .document(uid)
        .collection("agentSessions")
        .document(AGENT_COLLECTION)
        .collection("sessions")
        .document(session_key)
    )


def _log_collection(uid: str, session_key: str):
    db = _firestore_db()
    if db is None:
        return None
    return (
        db.collection("users")
        .document(uid)
        .collection("agentActivity")
        .document(AGENT_COLLECTION)
        .collection("sessions")
        .document(session_key)
        .collection("logs")
    )


def load_session(uid: str | None, session_key: str, max_age_seconds: int) -> tuple[dict[str, Any] | None, str | None]:
    if uid:
        ref = _session_doc(uid, session_key)
        if ref is not None:
            try:
                snapshot = ref.get()
                if snapshot.exists:
                    data = snapshot.to_dict() or {}
                    payload = data.get("payload")
                    updated_at_iso = str(data.get("updatedAtIso") or data.get("createdAtIso") or "").strip()
                    if isinstance(payload, dict) and updated_at_iso:
                        updated_at = datetime.fromisoformat(updated_at_iso)
                        if _utc_now() - updated_at <= timedelta(seconds=max_age_seconds):
                            return payload, "firestore"
            except Exception:
                pass

    data = _read_local_store()
    owner = uid or "anonymous"
    record = data.get("sessions", {}).get(owner, {}).get(session_key)
    if not isinstance(record, dict):
        return None, None
    payload = record.get("payload")
    updated_at_iso = str(record.get("updatedAtIso") or record.get("createdAtIso") or "").strip()
    if not isinstance(payload, dict) or not updated_at_iso:
        return None, None
    try:
        updated_at = datetime.fromisoformat(updated_at_iso)
    except ValueError:
        return None, None
    if _utc_now() - updated_at > timedelta(seconds=max_age_seconds):
        return None, None
    return payload, "local"


def save_session(uid: str | None, session_key: str, payload: dict[str, Any]) -> None:
    now_iso = _utc_iso()
    if uid:
        ref = _session_doc(uid, session_key)
        if ref is not None:
            try:
                ref.set(
                    {
                        "sessionKey": session_key,
                        "agentId": AGENT_COLLECTION,
                        "payload": payload,
                        "updatedAtIso": now_iso,
                        "createdAtIso": now_iso,
                    },
                    merge=True,
                )
                return
            except Exception:
                pass

    data = _read_local_store()
    owner = uid or "anonymous"
    data.setdefault("sessions", {})
    data["sessions"].setdefault(owner, {})
    data["sessions"][owner][session_key] = {
        "payload": payload,
        "updatedAtIso": now_iso,
        "createdAtIso": now_iso,
    }
    _write_local_store(data)


def reset_session(uid: str | None, session_key: str) -> None:
    if uid:
        ref = _session_doc(uid, session_key)
        if ref is not None:
            try:
                ref.delete()
            except Exception:
                pass

    data = _read_local_store()
    owner = uid or "anonymous"
    owner_sessions = data.get("sessions", {}).get(owner, {})
    if isinstance(owner_sessions, dict):
        owner_sessions.pop(session_key, None)
    _write_local_store(data)


def save_interaction_log(
    uid: str | None,
    session_key: str,
    *,
    action: str,
    status: str,
    payload: dict[str, Any],
) -> None:
    now_iso = _utc_iso()
    log_doc = {
        "agentId": AGENT_COLLECTION,
        "sessionKey": session_key,
        "action": action,
        "status": status,
        "payload": payload,
        "createdAtIso": now_iso,
    }

    if uid:
        collection = _log_collection(uid, session_key)
        if collection is not None:
            try:
                collection.document().set(log_doc, merge=True)
                return
            except Exception:
                pass

    data = _read_local_store()
    owner = uid or "anonymous"
    data.setdefault("logs", {})
    data["logs"].setdefault(owner, {})
    data["logs"][owner].setdefault(session_key, [])
    data["logs"][owner][session_key].insert(0, log_doc)
    data["logs"][owner][session_key] = data["logs"][owner][session_key][:50]
    _write_local_store(data)


def list_session_logs(uid: str | None, session_key: str, limit: int = 12) -> list[dict[str, Any]]:
    if uid:
        collection = _log_collection(uid, session_key)
        if collection is not None:
            try:
                docs = (
                    collection.order_by("createdAtIso", direction=firestore.Query.DESCENDING)
                    .limit(limit)
                    .stream()
                )
                return [doc.to_dict() or {} for doc in docs]
            except Exception:
                pass

    data = _read_local_store()
    owner = uid or "anonymous"
    rows = data.get("logs", {}).get(owner, {}).get(session_key, [])
    if not isinstance(rows, list):
        return []
    return rows[:limit]
