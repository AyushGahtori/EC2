from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
except Exception:  # pragma: no cover
    firebase_admin = None
    credentials = None
    firestore = None

_ADMIN_APP = None


def _resolve_key_path() -> str:
    return (
        os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY")
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        or "./serviceAccountKey.json"
    )


def _get_admin_db():
    global _ADMIN_APP
    if firebase_admin is None or credentials is None or firestore is None:
        return None

    if _ADMIN_APP is not None:
        return firestore.client(_ADMIN_APP)

    if not firebase_admin._apps:
        key_path = Path(_resolve_key_path()).expanduser().resolve()
        if not key_path.exists():
            return None
        service_account = json.loads(key_path.read_text(encoding="utf-8"))
        _ADMIN_APP = firebase_admin.initialize_app(credentials.Certificate(service_account))
    else:
        _ADMIN_APP = firebase_admin.get_app()

    return firestore.client(_ADMIN_APP)


async def save_dashboard_artifact(uid: str, payload: dict[str, Any]) -> None:
    db = _get_admin_db()
    if db is None:
        return

    doc_id = str(payload.get("artifactId") or payload.get("requestId") or "latest")
    user_ref = db.collection("users").document(uid)
    history_ref = user_ref.collection("dashboardDesigner").document(doc_id)

    document = {
        "artifactId": doc_id,
        "agentId": payload.get("agentId") or "dashboard-designer-agent",
        "displayName": payload.get("displayName") or "Dashboard Designer",
        "status": payload.get("status") or "success",
        "summary": payload.get("summary") or "",
        "prompt": payload.get("prompt") or "",
        "projectContext": payload.get("projectContext") or "",
        "dashboardSchema": payload.get("dashboardSchema") or {},
        "requestSummary": payload.get("requestSummary") or {},
        "analysis": payload.get("analysis") or {},
        "createdAt": firestore.SERVER_TIMESTAMP,
        "updatedAt": firestore.SERVER_TIMESTAMP,
    }

    history_ref.set(document, merge=True)
    user_ref.collection("dashboardDesignerState").document("latest").set(
        {
            "artifactId": doc_id,
            "updatedAt": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )

