"""
Redis service — async caching layer.
Used to cache: session data, product analysis results, agent checkpoints.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

from config.settings import get_settings

logger = logging.getLogger(__name__)

_redis_client: Optional[aioredis.Redis] = None


async def get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        logger.info(f"Redis connected: {settings.redis_url}")
    return _redis_client


async def redis_set(key: str, value: Any, ttl: Optional[int] = None) -> bool:
    """Store a value in Redis (auto-serialises dicts/lists to JSON)."""
    try:
        r = await get_redis()
        settings = get_settings()
        expiry = ttl or settings.redis_ttl

        if isinstance(value, (dict, list)):
            value = json.dumps(value)

        await r.set(key, value, ex=expiry)
        return True
    except Exception as e:
        logger.error(f"Redis SET failed: key={key!r} error={e}")
        return False


async def redis_get(key: str, as_json: bool = False) -> Any:
    """Retrieve a value from Redis."""
    try:
        r = await get_redis()
        value = await r.get(key)
        if value is None:
            return None
        if as_json:
            return json.loads(value)
        return value
    except Exception as e:
        logger.error(f"Redis GET failed: key={key!r} error={e}")
        return None


async def redis_delete(key: str) -> bool:
    try:
        r = await get_redis()
        await r.delete(key)
        return True
    except Exception as e:
        logger.error(f"Redis DELETE failed: key={key!r} error={e}")
        return False


async def redis_exists(key: str) -> bool:
    try:
        r = await get_redis()
        return bool(await r.exists(key))
    except Exception:
        return False


async def cache_product_analysis(session_id: str, analysis: str) -> None:
    await redis_set(f"product:{session_id}:analysis", analysis, ttl=86400)


async def get_cached_product_analysis(session_id: str) -> Optional[str]:
    return await redis_get(f"product:{session_id}:analysis")


async def cache_session_context(session_id: str, context: dict) -> None:
    await redis_set(f"session:{session_id}:context", context, ttl=86400)


async def get_session_context(session_id: str) -> Optional[dict]:
    return await redis_get(f"session:{session_id}:context", as_json=True)


async def health_check() -> bool:
    try:
        r = await get_redis()
        await r.ping()
        return True
    except Exception:
        return False
