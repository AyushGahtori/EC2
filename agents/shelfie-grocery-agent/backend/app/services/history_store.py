from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection

logger = logging.getLogger("shelfie-grocery-agent.history-store")


class MongoHistoryStore:
    def __init__(
        self,
        mongodb_url: str,
        db_name: str,
        collection_name: str,
    ) -> None:
        self._mongodb_url = mongodb_url
        self._db_name = db_name
        self._collection_name = collection_name
        self._client: AsyncIOMotorClient | None = None
        self._collection: AsyncIOMotorCollection | None = None
        self._fallback_path = Path(__file__).resolve().parents[2] / "runtime_data" / "history_store.json"
        self._use_file_fallback = False
        self._fallback_lock = threading.Lock()

    @property
    def collection(self) -> AsyncIOMotorCollection:
        if self._collection is None:
            raise RuntimeError("MongoHistoryStore is not connected.")
        return self._collection

    async def connect(self) -> None:
        try:
            self._client = AsyncIOMotorClient(self._mongodb_url, serverSelectionTimeoutMS=3000)
            self._collection = self._client[self._db_name][self._collection_name]
            await self._client.admin.command("ping")
            await self.collection.create_index([("session_id", 1), ("created_at", 1)])
            await self.collection.create_index([("user_id", 1), ("created_at", 1)])
            self._use_file_fallback = False
        except Exception as exc:  # pragma: no cover - depends on runtime infra
            logger.warning("MongoDB unavailable, using file-backed history store: %s", exc)
            self._client = None
            self._collection = None
            self._use_file_fallback = True
            self._ensure_fallback_store()

    async def disconnect(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            self._collection = None

    def _ensure_fallback_store(self) -> None:
        self._fallback_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._fallback_path.exists():
            self._fallback_path.write_text(
                json.dumps({"messages": [], "sessions": {}}, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )

    def _read_fallback(self) -> dict:
        self._ensure_fallback_store()
        raw = self._fallback_path.read_text(encoding="utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"messages": [], "sessions": {}}
        payload.setdefault("messages", [])
        payload.setdefault("sessions", {})
        return payload

    def _write_fallback(self, payload: dict) -> None:
        self._fallback_path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )

    async def append_message(
        self,
        session_id: str,
        user_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        created_at = datetime.now(timezone.utc)
        doc = {
            "session_id": session_id,
            "user_id": user_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
            "created_at": created_at,
            "created_at_iso": created_at.isoformat(),
        }

        if self._use_file_fallback:
            with self._fallback_lock:
                payload = self._read_fallback()
                payload["messages"].append(
                    {
                        "session_id": session_id,
                        "user_id": user_id,
                        "role": role,
                        "content": content,
                        "metadata": metadata or {},
                        "created_at_iso": created_at.isoformat(),
                    }
                )
                payload["sessions"][session_id] = {
                    "session_id": session_id,
                    "user_id": user_id,
                    "last_message": content,
                    "last_role": role,
                    "last_updated_iso": created_at.isoformat(),
                    "message_count": int(payload["sessions"].get(session_id, {}).get("message_count", 0)) + 1,
                }
                self._write_fallback(payload)
            return

        await self.collection.insert_one(doc)

    async def load_messages(
        self,
        session_id: str,
        limit: int = 200,
    ) -> list[dict[str, str]]:
        if self._use_file_fallback:
            payload = self._read_fallback()
            docs = [
                d for d in payload["messages"]
                if d.get("session_id") == session_id
            ]
            docs = docs[-limit:]
            return [{"role": str(d.get("role", "")), "content": str(d.get("content", ""))} for d in docs]

        cursor = (
            self.collection.find({"session_id": session_id})
            .sort("created_at", 1)
            .limit(limit)
        )
        docs = await cursor.to_list(length=limit)
        return [{"role": d["role"], "content": d["content"]} for d in docs]

    async def list_sessions(
        self,
        user_id: str,
        limit: int = 20,
    ) -> list[dict]:
        if self._use_file_fallback:
            payload = self._read_fallback()
            sessions = [
                value
                for value in payload["sessions"].values()
                if value.get("user_id") == user_id
            ]
            sessions.sort(key=lambda item: str(item.get("last_updated_iso", "")), reverse=True)
            items = sessions[: max(1, min(limit, 100))]
            return [
                {
                    "session_id": item.get("session_id", ""),
                    "last_message": item.get("last_message", ""),
                    "last_role": item.get("last_role", ""),
                    "last_updated": item.get("last_updated_iso", datetime.now(timezone.utc).isoformat()),
                    "message_count": int(item.get("message_count", 0)),
                }
                for item in items
            ]

        pipeline = [
            {"$match": {"user_id": user_id}},
            {"$sort": {"created_at": -1}},
            {
                "$group": {
                    "_id": "$session_id",
                    "last_message": {"$first": "$content"},
                    "last_role": {"$first": "$role"},
                    "last_updated": {"$first": "$created_at"},
                    "message_count": {"$sum": 1},
                }
            },
            {"$sort": {"last_updated": -1}},
            {"$limit": max(1, min(limit, 100))},
            {
                "$project": {
                    "_id": 0,
                    "session_id": "$_id",
                    "last_message": 1,
                    "last_role": 1,
                    "last_updated": 1,
                    "message_count": 1,
                }
            },
        ]

        cursor = self.collection.aggregate(pipeline)
        return await cursor.to_list(length=max(1, min(limit, 100)))

    async def clear_session(self, session_id: str) -> None:
        if self._use_file_fallback:
            with self._fallback_lock:
                payload = self._read_fallback()
                payload["messages"] = [d for d in payload["messages"] if d.get("session_id") != session_id]
                payload["sessions"].pop(session_id, None)
                self._write_fallback(payload)
            return

        await self.collection.delete_many({"session_id": session_id})
