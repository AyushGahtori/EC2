from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    APP_NAME: str = "Shelfie Agent API"
    ENVIRONMENT: str = "development"
    API_V1_PREFIX: str = "/api/v1"
    FRONTEND_ORIGIN: str = "http://localhost:3000"

    LLM_PROVIDER: Literal["ollama", "ollama_cloud", "openai", "anthropic", "gemini"] = "gemini"
    LLM_TEMPERATURE: float = Field(default=0.2, ge=0.0, le=1.0)
    LLM_MAX_TOKENS: int = Field(default=1024, ge=128, le=8192)

    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "gemma4:31b-cloud"

    OLLAMA_CLOUD_BASE_URL: str = "https://your-ollama-cloud-openai-endpoint/v1"
    OLLAMA_CLOUD_API_KEY: str | None = None
    OLLAMA_CLOUD_MODEL: str = "gemma4:31b-cloud"

    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL: str = "gpt-4.1-mini"

    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_MODEL: str = "claude-3-5-sonnet-latest"

    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-2.5-flash"

    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_TTL_SECONDS: int = 60 * 60 * 24

    MONGODB_URL: str = "mongodb://localhost:27017"
    MONGODB_DB_NAME: str = "shelfie_agent"
    MONGODB_MESSAGES_COLLECTION: str = "messages"

    @property
    def active_model(self) -> str:
        if self.LLM_PROVIDER == "ollama":
            return self.OLLAMA_MODEL
        if self.LLM_PROVIDER == "ollama_cloud":
            return self.OLLAMA_CLOUD_MODEL
        if self.LLM_PROVIDER == "openai":
            return self.OPENAI_MODEL
        if self.LLM_PROVIDER == "anthropic":
            return self.ANTHROPIC_MODEL
        return self.GEMINI_MODEL


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
