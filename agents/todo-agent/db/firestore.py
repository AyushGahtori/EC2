"""
db/firestore.py — Firebase Firestore persistence for To-Do Agent.
Replaces MongoDB, enforces userId isolation.
"""
import logging
import uuid
from typing import Any

import firebase_admin
from firebase_admin import credentials, firestore

logger = logging.getLogger(__name__)

import os

# Try to find the service account key in the repo root (ai-everyone/serviceAccountKey.json)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
fallback_key_path = os.path.join(BASE_DIR, "serviceAccountKey.json")
secrets_key_path = "/app/.secrets/serviceAccountKey.json"

key_path = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY") or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or secrets_key_path or fallback_key_path

# Initialize Firebase Admin if not already initialized
try:
    firebase_admin.get_app()
    logger.info("Firebase Admin already initialized.")
except ValueError:
    if os.path.exists(key_path):
        cred = credentials.Certificate(key_path)
        firebase_admin.initialize_app(cred)
        logger.info(f"Firebase Admin initialized with local key: {key_path}")
    else:
        # Fallback to pure default credentials 
        firebase_admin.initialize_app()
        logger.info("Firebase Admin initialized with default credentials.")

db = firestore.client()
todos_ref = db.collection(u"todos")

class DatabaseError(Exception):
    """Raised when a DB operation fails unexpectedly."""
    pass

def _serialize(doc) -> dict:
    data = doc.to_dict()
    data["_id"] = doc.id
    if "createdAt" in data and hasattr(data["createdAt"], "isoformat"):
        data["createdAt"] = data["createdAt"].isoformat()
    return data

def add_task(user_id: str, task: dict) -> str:
    try:
        doc_id = str(uuid.uuid4())
        doc_ref = todos_ref.document(doc_id)
        
        task_data = {
            "userId": user_id,
            "title": task.get("title", ""),
            "datetime": task.get("datetime", ""),
            "status": task.get("status", "pending"),
            "priority": task.get("priority", "normal"),
            "tags": task.get("tags", []),
            "createdAt": firestore.SERVER_TIMESTAMP,
        }
        doc_ref.set(task_data)
        return doc_id
    except Exception as exc:
        raise DatabaseError(f"add_task failed: {exc}")

def get_tasks(user_id: str, status: str | None = None) -> list[dict]:
    try:
        query = todos_ref.where("userId", "==", user_id)
        if status:
            query = query.where("status", "==", status)
        
        docs = query.get()
        return [_serialize(doc) for doc in docs]
    except Exception as exc:
        raise DatabaseError(f"get_tasks failed: {exc}")

def get_tasks_by_date(user_id: str, date_str: str) -> list[dict]:
    try:
        # Simplistic prefix match approach: fetch all and filter, or use a specific date field
        # Firestore doesn't do native 'startswith' easily on strings without >= and < tricks.
        # Since this is a simple string compare:
        end_str = date_str + u"\uf8ff"
        docs = todos_ref.where("userId", "==", user_id)\
                        .where("datetime", ">=", date_str)\
                        .where("datetime", "<", end_str).get()
        return [_serialize(doc) for doc in docs]
    except Exception as exc:
        raise DatabaseError(f"get_tasks_by_date failed: {exc}")

def get_task_by_id(user_id: str, task_id: str) -> dict | None:
    try:
        doc = todos_ref.document(task_id).get()
        if not doc.exists or doc.to_dict().get("userId") != user_id:
            return None
        return _serialize(doc)
    except Exception as exc:
        raise DatabaseError(f"get_task_by_id failed: {exc}")

def update_task(user_id: str, task_id: str, updated_data: dict) -> bool:
    try:
        doc_ref = todos_ref.document(task_id)
        doc = doc_ref.get()
        if not doc.exists or doc.to_dict().get("userId") != user_id:
            return False
            
        # exclude protected fields
        patch = {k: v for k, v in updated_data.items() if k not in ["userId", "_id", "createdAt"]}
        doc_ref.update(patch)
        return True
    except Exception as exc:
        raise DatabaseError(f"update_task failed: {exc}")

def mark_done(user_id: str, task_id: str) -> bool:
    return update_task(user_id, task_id, {"status": "done"})

def delete_task(user_id: str, task_id: str) -> bool:
    try:
        doc_ref = todos_ref.document(task_id)
        doc = doc_ref.get()
        if not doc.exists or doc.to_dict().get("userId") != user_id:
            return False
        doc_ref.delete()
        return True
    except Exception as exc:
        raise DatabaseError(f"delete_task failed: {exc}")

def delete_multiple_tasks(user_id: str, task_ids: list[str]) -> int:
    deleted = 0
    for tid in task_ids:
        if delete_task(user_id, tid):
            deleted += 1
    return deleted

def is_using_memory_db() -> bool:
    return False
