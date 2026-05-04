"""
Memory Service
- Redis  → short-term session memory (fast)
- MongoDB → long-term persistent storage
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis
from loguru import logger
from motor.motor_asyncio import AsyncIOMotorClient

_redis_client: Optional[aioredis.Redis] = None
_mongo_client: Optional[AsyncIOMotorClient] = None


# ──────────────────────────────────────────────
# Connection helpers
# ──────────────────────────────────────────────

async def get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        url = os.getenv("REDIS_URL", "redis://localhost:6379")
        _redis_client = await aioredis.from_url(url, decode_responses=True)
    return _redis_client


async def get_mongo():
    global _mongo_client
    if _mongo_client is None:
        url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
        _mongo_client = AsyncIOMotorClient(url)
    db_name = os.getenv("MONGODB_DB_NAME", "aria_podcast")
    return _mongo_client[db_name]


# ──────────────────────────────────────────────
# Session memory (Redis)
# ──────────────────────────────────────────────

SESSION_TTL = int(os.getenv("SESSION_TTL_SECONDS", "3600"))
SESSION_MAX_MESSAGES = 50  # keep last N messages per session


async def get_session_history(session_id: str) -> List[Dict]:
    """Return conversation history for a session."""
    try:
        r = await get_redis()
        key = f"session:{session_id}:history"
        raw = await r.lrange(key, -SESSION_MAX_MESSAGES, -1)
        return [json.loads(m) for m in raw]
    except Exception as exc:
        logger.warning(f"Redis read failed: {exc}")
        return []


async def append_to_session(session_id: str, role: str, content: str) -> None:
    """Append a message to session history."""
    try:
        r = await get_redis()
        key = f"session:{session_id}:history"
        msg = json.dumps({"role": role, "content": content, "ts": time.time()})
        await r.rpush(key, msg)
        await r.expire(key, SESSION_TTL)
    except Exception as exc:
        logger.warning(f"Redis write failed: {exc}")


async def clear_session(session_id: str) -> None:
    try:
        r = await get_redis()
        await r.delete(f"session:{session_id}:history")
    except Exception as exc:
        logger.warning(f"Redis clear failed: {exc}")


async def get_session_mode(session_id: str) -> str:
    """Get current mode (host/creator) for session."""
    try:
        r = await get_redis()
        mode = await r.get(f"session:{session_id}:mode")
        return mode or "creator"
    except Exception:
        return "creator"


async def set_session_mode(session_id: str, mode: str) -> None:
    try:
        r = await get_redis()
        await r.set(f"session:{session_id}:mode", mode, ex=SESSION_TTL)
    except Exception as exc:
        logger.warning(f"Redis mode set failed: {exc}")


# ──────────────────────────────────────────────
# Persistent storage (MongoDB)
# ──────────────────────────────────────────────

async def save_conversation(session_id: str, messages: List[Dict]) -> None:
    try:
        db = await get_mongo()
        await db.conversations.update_one(
            {"session_id": session_id},
            {
                "$set": {
                    "messages": messages,
                    "updated_at": time.time(),
                },
                "$setOnInsert": {"created_at": time.time()},
            },
            upsert=True,
        )
    except Exception as exc:
        logger.warning(f"MongoDB save failed: {exc}")


async def load_conversation(session_id: str) -> Optional[Dict]:
    try:
        db = await get_mongo()
        doc = await db.conversations.find_one({"session_id": session_id})
        return doc
    except Exception as exc:
        logger.warning(f"MongoDB load failed: {exc}")
        return None


async def list_conversations(limit: int = 20) -> List[Dict]:
    try:
        db = await get_mongo()
        cursor = db.conversations.find(
            {}, {"session_id": 1, "updated_at": 1, "created_at": 1}
        ).sort("updated_at", -1).limit(limit)
        return await cursor.to_list(length=limit)
    except Exception as exc:
        logger.warning(f"MongoDB list failed: {exc}")
        return []


async def health_check() -> Dict[str, bool]:
    status = {"redis": False, "mongodb": False}
    try:
        r = await get_redis()
        await r.ping()
        status["redis"] = True
    except Exception:
        pass
    try:
        db = await get_mongo()
        await db.command("ping")
        status["mongodb"] = True
    except Exception:
        pass
    return status
