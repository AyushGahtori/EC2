"""
MongoDB service — persistent storage for sessions, messages, and generated content.
Uses Motor (async MongoDB driver).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from config.settings import get_settings
from models.schemas import (
    GeneratedContentDocument,
    MessageDocument,
    MessageRole,
    ProductDocument,
    SessionDocument,
)

logger = logging.getLogger(__name__)

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None


async def get_db() -> AsyncIOMotorDatabase:
    global _client, _db
    if _db is None:
        settings = get_settings()
        _client = AsyncIOMotorClient(settings.mongodb_url)
        _db = _client[settings.mongodb_db_name]
        # Ensure indexes
        await _db.sessions.create_index("session_id", unique=True)
        await _db.messages.create_index([("session_id", 1), ("created_at", 1)])
        await _db.generated_content.create_index([("session_id", 1), ("created_at", -1)])
        await _db.products.create_index("session_id")
        logger.info(f"MongoDB connected: {settings.mongodb_db_name}")
    return _db


# ── Sessions ────────────────────────────────────────────────────────────────

async def create_session(session: SessionDocument) -> str:
    db = await get_db()
    await db.sessions.insert_one(session.model_dump())
    return session.session_id


async def get_session(session_id: str) -> Optional[dict]:
    db = await get_db()
    return await db.sessions.find_one({"session_id": session_id}, {"_id": 0})


async def update_session(session_id: str, updates: dict) -> None:
    db = await get_db()
    updates["updated_at"] = datetime.utcnow()
    await db.sessions.update_one({"session_id": session_id}, {"$set": updates})


async def list_sessions(limit: int = 50) -> list[dict]:
    db = await get_db()
    cursor = db.sessions.find({}, {"_id": 0}).sort("updated_at", -1).limit(limit)
    return await cursor.to_list(length=limit)


async def delete_session(session_id: str) -> None:
    db = await get_db()
    await db.sessions.delete_one({"session_id": session_id})
    await db.messages.delete_many({"session_id": session_id})
    await db.generated_content.delete_many({"session_id": session_id})
    await db.products.delete_many({"session_id": session_id})


# ── Messages ─────────────────────────────────────────────────────────────────

async def save_message(msg: MessageDocument) -> str:
    db = await get_db()
    result = await db.messages.insert_one(msg.model_dump())
    # Increment session message count
    await db.sessions.update_one(
        {"session_id": msg.session_id},
        {"$inc": {"message_count": 1}, "$set": {"updated_at": datetime.utcnow()}},
    )
    return str(result.inserted_id)


async def get_messages(session_id: str, limit: int = 100) -> list[dict]:
    db = await get_db()
    cursor = db.messages.find(
        {"session_id": session_id},
        {"_id": 1, "role": 1, "content": 1, "image_id": 1, "metadata": 1, "created_at": 1},
    ).sort("created_at", 1).limit(limit)
    docs = await cursor.to_list(length=limit)
    for d in docs:
        d["id"] = str(d.pop("_id"))
    return docs


# ── Generated Content ────────────────────────────────────────────────────────

async def save_content(content: GeneratedContentDocument) -> str:
    db = await get_db()
    result = await db.generated_content.insert_one(content.model_dump())
    return str(result.inserted_id)


async def get_content(session_id: str, limit: int = 20) -> list[dict]:
    db = await get_db()
    cursor = db.generated_content.find(
        {"session_id": session_id},
        {"_id": 1, "content_type": 1, "content": 1, "platform": 1, "metadata": 1, "created_at": 1},
    ).sort("created_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    for d in docs:
        d["id"] = str(d.pop("_id"))
    return docs


async def get_latest_content(session_id: str, content_type: str) -> Optional[dict]:
    """Return the most recent piece of generated content of the given type for a session."""
    db = await get_db()
    return await db.generated_content.find_one(
        {"session_id": session_id, "content_type": content_type},
        {"_id": 0, "content_type": 1, "content": 1, "platform": 1, "metadata": 1, "created_at": 1},
        sort=[("created_at", -1)],
    )


# ── Products ─────────────────────────────────────────────────────────────────

async def save_product(product: ProductDocument) -> str:
    db = await get_db()
    result = await db.products.insert_one(product.model_dump())
    return str(result.inserted_id)


async def get_product(session_id: str) -> Optional[dict]:
    db = await get_db()
    doc = await db.products.find_one(
        {"session_id": session_id},
        {"_id": 0},
        sort=[("created_at", -1)],
    )
    return doc


# ── Health ────────────────────────────────────────────────────────────────────

async def health_check() -> bool:
    try:
        db = await get_db()
        await db.command("ping")
        return True
    except Exception:
        return False
