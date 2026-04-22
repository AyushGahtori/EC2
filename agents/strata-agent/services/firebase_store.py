"""Firestore persistence layer for strata-agent."""

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

    root_fallback = Path(__file__).resolve().parents[3] / "serviceAccountKey.json"
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


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strata_doc(user_id: str):
    return _db.collection("users").document(user_id).collection("strata").document("meta")


def _subcollection(user_id: str, name: str):
    return _strata_doc(user_id).collection(name)


def save_snapshot(user_id: str, symbol: str, month: int, payload: dict[str, Any]) -> None:
    _subcollection(user_id, "snapshots").document(f"{symbol.upper()}-{month:02d}").set(
        {
            "symbol": symbol.upper(),
            "month": month,
            "payload": payload,
            "updatedAt": firestore.SERVER_TIMESTAMP,
            "updatedAtIso": _utcnow_iso(),
        },
        merge=True,
    )


def save_query(user_id: str, symbol: str, question: str, answer: str, context: dict[str, Any]) -> None:
    _subcollection(user_id, "queries").add(
        {
            "symbol": symbol.upper(),
            "question": question,
            "answer": answer,
            "context": context,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "createdAtIso": _utcnow_iso(),
        }
    )


def save_report(
    user_id: str,
    report_name: str,
    status: str,
    summary: str,
    processed_files: list[dict[str, Any]],
    failed_files: list[dict[str, Any]],
) -> None:
    _subcollection(user_id, "reports").add(
        {
            "reportName": report_name,
            "status": status,
            "summary": summary,
            "processedFiles": processed_files,
            "failedFiles": failed_files,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "createdAtIso": _utcnow_iso(),
        }
    )
