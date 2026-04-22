from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
except Exception:  # pragma: no cover - optional runtime dependency
    firebase_admin = None
    credentials = None
    firestore = None


_LOCK = threading.Lock()
_BASE_DIR = Path(__file__).resolve().parent
_LOCAL_DATA_DIR = _BASE_DIR / "data"
_LOCAL_DATA_PATH = _LOCAL_DATA_DIR / "store.json"
_FIREBASE_DB = None
_FIREBASE_READY = False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso() -> str:
    return _utc_now().isoformat()


def _resolve_key_path() -> str | None:
    env_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if env_path:
        return env_path

    local_path = _BASE_DIR / "serviceAccountKey.json"
    if local_path.exists():
        return str(local_path)

    ec2_path = "/home/ubuntu/app/.secrets/serviceAccountKey.json"
    if Path(ec2_path).exists():
        return ec2_path

    return None


def _ensure_firebase() -> bool:
    global _FIREBASE_DB
    global _FIREBASE_READY

    if _FIREBASE_READY and _FIREBASE_DB is not None:
        return True
    if firebase_admin is None or firestore is None:
        return False

    try:
        if not firebase_admin._apps:
            key_path = _resolve_key_path()
            if key_path and credentials is not None:
                firebase_admin.initialize_app(credentials.Certificate(key_path))
            else:
                firebase_admin.initialize_app()
        _FIREBASE_DB = firestore.client()
        _FIREBASE_READY = True
        return True
    except Exception:
        _FIREBASE_DB = None
        _FIREBASE_READY = False
        return False


def _ensure_local_store() -> None:
    _LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not _LOCAL_DATA_PATH.exists():
        _LOCAL_DATA_PATH.write_text(
            json.dumps({"snapshots": {}, "cache": {}}, indent=2),
            encoding="utf-8",
        )


def _read_local_store() -> dict[str, Any]:
    _ensure_local_store()
    with _LOCK:
        try:
            raw = _LOCAL_DATA_PATH.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"snapshots": {}, "cache": {}}


def _write_local_store(payload: dict[str, Any]) -> None:
    _ensure_local_store()
    with _LOCK:
        _LOCAL_DATA_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _cache_doc(user_id: str, cache_key: str):
    return (
        _FIREBASE_DB.collection("users")
        .document(user_id)
        .collection("devikaEngineerCache")
        .document(cache_key)
    )


def _snapshot_collection(user_id: str):
    return (
        _FIREBASE_DB.collection("users")
        .document(user_id)
        .collection("devikaEngineerSnapshots")
    )


def get_cached_result(
    user_id: str,
    cache_key: str,
    max_age_seconds: int,
) -> tuple[dict[str, Any] | None, str | None]:
    if _ensure_firebase():
        try:
            doc = _cache_doc(user_id, cache_key).get()
            if doc.exists:
                data = doc.to_dict() or {}
                payload = data.get("payload")
                cached_at_iso = str(data.get("updatedAtIso") or data.get("createdAtIso") or "").strip()
                if isinstance(payload, dict) and cached_at_iso:
                    cached_at = datetime.fromisoformat(cached_at_iso)
                    if _utc_now() - cached_at <= timedelta(seconds=max_age_seconds):
                        return payload, "store"
        except Exception:
            pass

    data = _read_local_store()
    user_cache = data.get("cache", {}).get(user_id, {})
    record = user_cache.get(cache_key)
    if not isinstance(record, dict):
        return None, None
    payload = record.get("payload")
    updated_at = str(record.get("updatedAtIso") or record.get("createdAtIso") or "").strip()
    if not isinstance(payload, dict) or not updated_at:
        return None, None
    try:
        cached_at = datetime.fromisoformat(updated_at)
    except ValueError:
        return None, None
    if _utc_now() - cached_at > timedelta(seconds=max_age_seconds):
        return None, None
    return payload, "store"


def save_cached_result(user_id: str, cache_key: str, payload: dict[str, Any]) -> None:
    now = _utc_iso()

    if _ensure_firebase():
        try:
            _cache_doc(user_id, cache_key).set(
                {
                    "payload": payload,
                    "createdAt": firestore.SERVER_TIMESTAMP,
                    "updatedAt": firestore.SERVER_TIMESTAMP,
                    "createdAtIso": now,
                    "updatedAtIso": now,
                },
                merge=True,
            )
            return
        except Exception:
            pass

    data = _read_local_store()
    data.setdefault("cache", {})
    data["cache"].setdefault(user_id, {})
    data["cache"][user_id][cache_key] = {
        "payload": payload,
        "createdAtIso": now,
        "updatedAtIso": now,
    }
    _write_local_store(data)


def save_snapshot(
    user_id: str,
    action: str,
    payload: dict[str, Any],
    status: str,
    cache_key: str | None = None,
) -> str:
    snapshot_id = f"devika-{int(_utc_now().timestamp() * 1000)}"
    now = _utc_iso()

    body = {
        "snapshotId": snapshot_id,
        "action": action,
        "status": status,
        "cacheKey": cache_key,
        "payload": payload,
        "createdAtIso": now,
        "updatedAtIso": now,
    }

    if _ensure_firebase():
        try:
            _snapshot_collection(user_id).document(snapshot_id).set(
                {
                    **body,
                    "createdAt": firestore.SERVER_TIMESTAMP,
                    "updatedAt": firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )
            return snapshot_id
        except Exception:
            pass

    data = _read_local_store()
    data.setdefault("snapshots", {})
    data["snapshots"].setdefault(user_id, [])
    data["snapshots"][user_id].insert(0, body)
    data["snapshots"][user_id] = data["snapshots"][user_id][:200]
    _write_local_store(data)
    return snapshot_id


def list_recent_snapshots(user_id: str, limit: int = 10) -> list[dict[str, Any]]:
    if _ensure_firebase():
        try:
            docs = (
                _snapshot_collection(user_id)
                .order_by("createdAtIso", direction=firestore.Query.DESCENDING)
                .limit(limit)
                .stream()
            )
            output: list[dict[str, Any]] = []
            for doc in docs:
                row = doc.to_dict() or {}
                row["snapshotId"] = row.get("snapshotId") or doc.id
                output.append(row)
            return output
        except Exception:
            pass

    data = _read_local_store()
    rows = data.get("snapshots", {}).get(user_id, [])
    if not isinstance(rows, list):
        return []
    return rows[:limit]


def get_status_summary(user_id: str, days: int = 7) -> dict[str, Any]:
    snapshots = list_recent_snapshots(user_id, limit=50)
    cutoff = _utc_now() - timedelta(days=days)

    counts = {"success": 0, "partial_success": 0, "needs_input": 0, "failed": 0, "action_required": 0}
    latest_action = None
    latest_iso = None

    for item in snapshots:
        created_at_iso = str(item.get("createdAtIso") or "").strip()
        try:
            created_at = datetime.fromisoformat(created_at_iso) if created_at_iso else None
        except ValueError:
            created_at = None
        if created_at and created_at < cutoff:
            continue

        status = str(item.get("status") or "").strip()
        if status in counts:
            counts[status] += 1
        if latest_iso is None and created_at_iso:
            latest_iso = created_at_iso
            latest_action = item.get("action")

    return {
        "windowDays": days,
        "counts": counts,
        "latestAction": latest_action,
        "latestActionAt": latest_iso,
    }
