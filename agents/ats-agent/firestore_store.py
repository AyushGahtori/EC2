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
    return _db.collection("users").document(user_id).collection("ats").document("meta")


def _candidates_col(user_id: str):
    return _meta_doc(user_id).collection("candidates")


def upsert_candidate(user_id: str, candidate_id: str | None, payload: dict[str, Any]) -> str:
    ref = _candidates_col(user_id).document(candidate_id) if candidate_id else _candidates_col(user_id).document()
    now = _utc_iso()
    clean_payload = {
        **payload,
        "updatedAt": firestore.SERVER_TIMESTAMP,
        "updatedAtIso": now,
    }
    if not candidate_id:
        clean_payload["createdAt"] = firestore.SERVER_TIMESTAMP
        clean_payload["createdAtIso"] = now
    ref.set(clean_payload, merge=True)
    return ref.id


def get_candidate(user_id: str, candidate_id: str) -> dict[str, Any] | None:
    snap = _candidates_col(user_id).document(candidate_id).get()
    if not snap.exists:
        return None
    doc = snap.to_dict() or {}
    doc["candidateId"] = snap.id
    return doc


def list_candidates(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    docs = _candidates_col(user_id).order_by("updatedAtIso", direction=firestore.Query.DESCENDING).limit(limit).stream()
    out: list[dict[str, Any]] = []
    for snap in docs:
        item = snap.to_dict() or {}
        item["candidateId"] = snap.id
        out.append(item)
    return out


def save_analysis(user_id: str, candidate_id: str, analysis: dict[str, Any]) -> None:
    upsert_candidate(
        user_id,
        candidate_id,
        {
            "latestAnalysis": analysis,
            "analysisHistory": firestore.ArrayUnion(
                [
                    {
                        "analysis": analysis,
                        "createdAtIso": _utc_iso(),
                    }
                ]
            ),
        },
    )


def save_question_set(user_id: str, candidate_id: str, stage: str, questions: list[dict[str, Any]]) -> None:
    upsert_candidate(
        user_id,
        candidate_id,
        {
            "latestQuestionSet": {"stage": stage, "questions": questions, "createdAtIso": _utc_iso()},
        },
    )


def save_transcript_feedback(
    user_id: str,
    candidate_id: str,
    stage: str,
    transcript: str,
    feedback: dict[str, Any],
) -> None:
    upsert_candidate(
        user_id,
        candidate_id,
        {
            "latestTranscript": {
                "stage": stage,
                "transcript": transcript,
                "createdAtIso": _utc_iso(),
            },
            "latestInterviewFeedback": feedback,
            "feedbackHistory": firestore.ArrayUnion(
                [
                    {
                        "stage": stage,
                        "feedback": feedback,
                        "createdAtIso": _utc_iso(),
                    }
                ]
            ),
        },
    )
