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
        return env_path

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


def _meta_doc(user_id: str):
    return _db.collection("users").document(user_id).collection("buildingConstruction").document("meta")


def _plans_col(user_id: str):
    return _meta_doc(user_id).collection("plans")


def save_plan(user_id: str, payload: dict[str, Any]) -> str:
    doc_ref = _plans_col(user_id).document()
    now = _utc_iso()
    doc_ref.set(
        {
            "payload": payload,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "createdAtIso": now,
            "updatedAt": firestore.SERVER_TIMESTAMP,
            "updatedAtIso": now,
        },
        merge=True,
    )
    return doc_ref.id


def list_recent_plans(user_id: str, limit: int = 10) -> list[dict[str, Any]]:
    docs = _plans_col(user_id).order_by("createdAtIso", direction=firestore.Query.DESCENDING).limit(limit).stream()
    out: list[dict[str, Any]] = []
    for snap in docs:
        item = snap.to_dict() or {}
        item["planId"] = snap.id
        out.append(item)
    return out
