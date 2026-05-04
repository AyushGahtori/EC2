"""
Redis client for conversation memory and API response caching.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

from app.config.settings import settings

logger = logging.getLogger(__name__)

_redis_client: Optional[aioredis.Redis] = None


async def get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_client


async def cache_set(key: str, value: Any, ttl: int = 3600) -> None:
    try:
        r = await get_redis()
        await r.setex(key, ttl, json.dumps(value, default=str))
    except Exception as e:
        logger.warning(f"Redis cache_set failed for key={key}: {e}")


async def cache_get(key: str) -> Optional[Any]:
    try:
        r = await get_redis()
        data = await r.get(key)
        return json.loads(data) if data else None
    except Exception as e:
        logger.warning(f"Redis cache_get failed for key={key}: {e}")
        return None


async def conversation_append(session_id: str, role: str, content: str) -> None:
    try:
        r = await get_redis()
        key = f"conv:{session_id}"
        existing = await r.get(key)
        messages = json.loads(existing) if existing else []
        messages.append({"role": role, "content": content})
        # Keep last 50 messages
        messages = messages[-50:]
        await r.setex(key, 86400, json.dumps(messages))
    except Exception as e:
        logger.warning(f"Redis conversation_append failed: {e}")


async def conversation_get(session_id: str) -> list[dict]:
    try:
        r = await get_redis()
        key = f"conv:{session_id}"
        data = await r.get(key)
        return json.loads(data) if data else []
    except Exception as e:
        logger.warning(f"Redis conversation_get failed: {e}")
        return []


async def conversation_clear(session_id: str) -> None:
    try:
        r = await get_redis()
        await r.delete(f"conv:{session_id}")
    except Exception as e:
        logger.warning(f"Redis conversation_clear failed: {e}")
