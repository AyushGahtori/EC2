from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from threading import RLock
from typing import Any


@dataclass
class CacheEntry:
    value: dict[str, Any]
    expires_at: float


class TTLCache:
    def __init__(self, *, ttl_seconds: int, max_entries: int = 512) -> None:
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._max_entries = max(1, int(max_entries))
        self._store: dict[str, CacheEntry] = {}
        self._lock = RLock()

    def get(self, key: str) -> dict[str, Any] | None:
        now = time.time()
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            if entry.expires_at <= now:
                self._store.pop(key, None)
                return None
            return entry.value

    def set(self, key: str, value: dict[str, Any]) -> None:
        now = time.time()
        with self._lock:
            if len(self._store) >= self._max_entries:
                oldest_key = min(self._store, key=lambda cache_key: self._store[cache_key].expires_at)
                self._store.pop(oldest_key, None)
            self._store[key] = CacheEntry(value=value, expires_at=now + self._ttl_seconds)


def make_cache_key(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
