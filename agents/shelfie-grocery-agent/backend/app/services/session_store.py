from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from redis.asyncio import Redis
from redis.asyncio import from_url as redis_from_url

logger = logging.getLogger("shelfie-grocery-agent.session-store")


class RedisSessionStore:
    def __init__(self, redis_url: str, ttl_seconds: int = 86_400) -> None:
        self._redis_url = redis_url
        self._ttl_seconds = ttl_seconds
        self._client: Redis | None = None
        self._memory_store: dict[str, tuple[datetime, list[dict[str, str]]]] = {}
        self._use_memory_fallback = False

    @property
    def client(self) -> Redis:
        if self._client is None:
            raise RuntimeError("RedisSessionStore is not connected.")
        return self._client

    async def connect(self) -> None:
        try:
            self._client = redis_from_url(
                self._redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            await self._client.ping()
            self._use_memory_fallback = False
        except Exception as exc:  # pragma: no cover - depends on runtime infra
            logger.warning("Redis unavailable, falling back to in-memory session cache: %s", exc)
            self._client = None
            self._use_memory_fallback = True

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._memory_store.clear()
        self._use_memory_fallback = False

    def _key(self, session_id: str) -> str:
        return f"shelfie:chat:{session_id}"

    def _expire_memory_key(self, session_id: str) -> None:
        payload = self._memory_store.get(session_id)
        if payload is None:
            return
        expires_at, _messages = payload
        if expires_at <= datetime.now(timezone.utc):
            self._memory_store.pop(session_id, None)

    def _memory_get(self, session_id: str) -> list[dict[str, str]] | None:
        self._expire_memory_key(session_id)
        payload = self._memory_store.get(session_id)
        if payload is None:
            return None
        _expires_at, messages = payload
        return messages

    def _memory_set(self, session_id: str, messages: list[dict[str, str]]) -> None:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self._ttl_seconds)
        self._memory_store[session_id] = (expires_at, messages)

    async def get_messages(self, session_id: str) -> list[dict[str, str]] | None:
        if self._use_memory_fallback:
            return self._memory_get(session_id)

        raw = await self.client.get(self._key(session_id))
        if not raw:
            return None
        return json.loads(raw)

    async def set_messages(self, session_id: str, messages: list[dict[str, str]]) -> None:
        if self._use_memory_fallback:
            self._memory_set(session_id, messages)
            return

        payload = json.dumps(messages, ensure_ascii=True)
        await self.client.set(self._key(session_id), payload, ex=self._ttl_seconds)

    async def clear_messages(self, session_id: str) -> None:
        if self._use_memory_fallback:
            self._memory_store.pop(session_id, None)
            return

        await self.client.delete(self._key(session_id))
