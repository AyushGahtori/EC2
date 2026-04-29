from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_chat import router as chat_router
from app.api.routes_health import router as health_router
from app.core.config import get_settings
from app.services.agent_service import AgentService
from app.services.history_store import MongoHistoryStore
from app.services.session_store import RedisSessionStore


def create_app() -> FastAPI:
    settings = get_settings()
    session_store = RedisSessionStore(
        redis_url=settings.REDIS_URL,
        ttl_seconds=settings.REDIS_TTL_SECONDS,
    )
    history_store = MongoHistoryStore(
        mongodb_url=settings.MONGODB_URL,
        db_name=settings.MONGODB_DB_NAME,
        collection_name=settings.MONGODB_MESSAGES_COLLECTION,
    )
    agent_service = AgentService(
        settings=settings,
        session_store=session_store,
        history_store=history_store,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await session_store.connect()
        await history_store.connect()
        app.state.settings = settings
        app.state.agent_service = agent_service
        yield
        await session_store.disconnect()
        await history_store.disconnect()

    app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.FRONTEND_ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router, prefix=settings.API_V1_PREFIX)
    app.include_router(chat_router, prefix=settings.API_V1_PREFIX)
    return app


app = create_app()

