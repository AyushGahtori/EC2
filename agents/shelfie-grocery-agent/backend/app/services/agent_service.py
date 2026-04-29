from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app.core.config import Settings
from app.llm.graph import build_react_graph
from app.services.history_store import MongoHistoryStore
from app.services.session_store import RedisSessionStore


class AgentService:
    def __init__(
        self,
        settings: Settings,
        session_store: RedisSessionStore,
        history_store: MongoHistoryStore,
    ) -> None:
        self._settings = settings
        self._session_store = session_store
        self._history_store = history_store
        self._graph = build_react_graph(settings)

    async def _load_context(self, session_id: str) -> list[dict[str, str]]:
        cached_messages = await self._session_store.get_messages(session_id)
        if cached_messages is not None:
            return cached_messages

        persisted_messages = await self._history_store.load_messages(session_id)
        if persisted_messages:
            await self._session_store.set_messages(session_id, persisted_messages)
        return persisted_messages

    async def get_history(self, session_id: str) -> list[dict[str, str]]:
        history = await self._session_store.get_messages(session_id)
        if history is not None:
            return history

        history = await self._history_store.load_messages(session_id)
        if history:
            await self._session_store.set_messages(session_id, history)
        return history

    async def list_sessions(self, user_id: str, limit: int = 20) -> list[dict]:
        return await self._history_store.list_sessions(user_id=user_id, limit=limit)

    async def generate_reply(
        self,
        session_id: str,
        user_id: str,
        user_message: str,
    ) -> str:
        context = await self._load_context(session_id)
        context.append({"role": "user", "content": user_message})
        result: dict[str, Any] | None = None
        last_error: Exception | None = None
        for _attempt in range(2):
            try:
                result = await self._graph.ainvoke(
                    {"messages": [self._to_langchain_message(m) for m in context]},
                    config={"configurable": {"thread_id": session_id}},
                )
                break
            except Exception as exc:
                last_error = exc

        if result is None:
            raise RuntimeError("LLM invocation failed after retry.") from last_error

        assistant_text = self._extract_assistant_text(result.get("messages", []))
        context.append({"role": "assistant", "content": assistant_text})

        await self._session_store.set_messages(session_id, context)
        await self._history_store.append_message(
            session_id=session_id,
            user_id=user_id,
            role="user",
            content=user_message,
        )
        await self._history_store.append_message(
            session_id=session_id,
            user_id=user_id,
            role="assistant",
            content=assistant_text,
            metadata={
                "provider": self._settings.LLM_PROVIDER,
                "model": self._settings.active_model,
            },
        )
        return assistant_text

    async def reset_session(self, session_id: str) -> None:
        await self._session_store.clear_messages(session_id)
        await self._history_store.clear_session(session_id)

    @staticmethod
    def _to_langchain_message(message: dict[str, str]) -> BaseMessage:
        role = message["role"]
        content = message["content"]

        if role == "assistant":
            return AIMessage(content=content)
        if role == "system":
            return SystemMessage(content=content)
        return HumanMessage(content=content)

    @staticmethod
    def _extract_assistant_text(messages: list[BaseMessage]) -> str:
        assistant_message = next(
            (m for m in reversed(messages) if isinstance(m, AIMessage)),
            None,
        )
        if assistant_message is None:
            return "I could not generate a response this time."
        return AgentService._coerce_content_to_text(assistant_message.content)

    @staticmethod
    def _coerce_content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("text", "")))
            return "\n".join([p for p in parts if p]).strip()

        return str(content)
