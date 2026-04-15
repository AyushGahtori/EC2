"""
database.py — Firestore connection manager using Firebase Admin.
Provides a singleton client and a dependency injection helper for FastAPI.
"""

import logging
import os
from pathlib import Path
from typing import Any

import firebase_admin
from firebase_admin import credentials, firestore

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

BASE_DIR = Path(__file__).resolve().parent
fallback_key_path = BASE_DIR / "serviceAccountKey.json"

_db: firestore.Client | None = None


def _init_firestore() -> firestore.Client:
    global _db
    if _db is not None:
        return _db

    key_path = Path(
        os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY")
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        or fallback_key_path
    )

    try:
        firebase_admin.get_app()
        logger.info("Firebase Admin already initialized.")
    except ValueError:
        if key_path.exists():
            cred = credentials.Certificate(str(key_path))
            firebase_admin.initialize_app(cred)
            logger.info(f"Firebase Admin initialized with key: {key_path}")
        else:
            firebase_admin.initialize_app()
            logger.info("Firebase Admin initialized with default credentials.")

    _db = firestore.client()
    return _db


def get_firestore_client() -> firestore.Client:
    return _init_firestore()


def get_roles_collection() -> Any:
    """Returns the Firestore collection for O*NET role documents."""
    client = get_firestore_client()
    return client.collection(settings.FIRESTORE_ROLES_COLLECTION)


def ensure_indexes():
    """
    Firestore does not require manual index creation here.
    This stub keeps compatibility with the existing startup flow.
    """
    logger.info("✅ Firestore index creation is not required for this agent.")


def ping_database() -> bool:
    """Health check — returns True if Firestore is reachable."""
    try:
        coll = get_roles_collection()
        coll.limit(1).get()
        return True
    except Exception as e:
        logger.error(f"Firestore ping failed: {e}")
        return False


def close_database():
    """No-op for Firestore. Kept for compatibility with existing app lifecycle."""
    logger.info("🔌 Firestore client shutdown is handled by firebase_admin automatically.")
