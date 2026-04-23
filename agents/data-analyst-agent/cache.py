from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any


class TTLCache:
    def __init__(self, ttl_seconds: int, max_entries: int) -> None:
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.max_entries = max(8, int(max_entries))
        self._store: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> dict[str, Any] | None:
        now = time.time()
        with self._lock:
            item = self._store.get(key)
            if not item:
                return None
            expires_at, payload = item
            if now >= expires_at:
                self._store.pop(key, None)
                return None
            self._store.move_to_end(key)
            return payload

    def set(self, key: str, payload: dict[str, Any]) -> None:
        now = time.time()
        expires_at = now + self.ttl_seconds
        with self._lock:
            self._store[key] = (expires_at, payload)
            self._store.move_to_end(key)
            while len(self._store) > self.max_entries:
                self._store.popitem(last=False)
            self._evict_expired(now)

    def _evict_expired(self, now: float) -> None:
        expired = [key for key, (expires_at, _) in self._store.items() if now >= expires_at]
        for key in expired:
            self._store.pop(key, None)


def make_cache_key(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
