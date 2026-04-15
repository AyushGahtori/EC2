from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import firebase_admin
from firebase_admin import credentials, firestore


def _resolve_key_path() -> str | None:
    env_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if env_path:
        candidate = Path(env_path).expanduser()
        if not candidate.is_absolute():
            candidate = (Path(__file__).resolve().parent / candidate).resolve()
        if candidate.exists():
            return str(candidate)

    root_fallback = Path(__file__).resolve().parents[2] / "serviceAccountKey.json"
    if root_fallback.exists():
        return str(root_fallback)

    ec2_fallback = "/home/ubuntu/app/.secrets/serviceAccountKey.json"
    if Path(ec2_fallback).exists():
        return ec2_fallback
    return None


def _ensure_firebase() -> None:
    if firebase_admin._apps:
        return
    key_path = _resolve_key_path()
    if key_path:
        firebase_admin.initialize_app(credentials.Certificate(key_path))
        return
    firebase_admin.initialize_app()


_ensure_firebase()
_db = firestore.client()


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lms_doc(user_id: str):
    return _db.collection("users").document(user_id).collection("lms").document("meta")


def _subcollection(user_id: str, name: str):
    return _lms_doc(user_id).collection(name)


def list_entities(user_id: str, collection_name: str, limit: int = 500) -> list[dict[str, Any]]:
    docs = _subcollection(user_id, collection_name).limit(limit).stream()
    out: list[dict[str, Any]] = []
    for doc in docs:
        item = doc.to_dict() or {}
        item["id"] = doc.id
        out.append(item)
    return out


def get_entity_by_field(
    user_id: str,
    collection_name: str,
    field_name: str,
    field_value: Any,
) -> dict[str, Any] | None:
    docs = (
        _subcollection(user_id, collection_name)
        .where(field_name, "==", field_value)
        .limit(1)
        .stream()
    )
    for doc in docs:
        item = doc.to_dict() or {}
        item["id"] = doc.id
        return item
    return None


def save_snapshot(user_id: str, snapshot_type: str, payload: dict[str, Any]) -> str:
    ref = _subcollection(user_id, "snapshots").document()
    now = _utc_iso()
    ref.set(
        {
            "type": snapshot_type,
            "payload": payload,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "createdAtIso": now,
            "updatedAt": firestore.SERVER_TIMESTAMP,
            "updatedAtIso": now,
        },
        merge=True,
    )
    return ref.id


def list_recent_snapshots(user_id: str, limit: int = 10) -> list[dict[str, Any]]:
    docs = _subcollection(user_id, "snapshots").order_by("createdAtIso", direction=firestore.Query.DESCENDING).limit(limit).stream()
    out: list[dict[str, Any]] = []
    for doc in docs:
        item = doc.to_dict() or {}
        item["snapshotId"] = doc.id
        out.append(item)
    return out
