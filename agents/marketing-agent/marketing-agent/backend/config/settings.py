"""
Application settings — loaded from environment variables.
Supports multiple LLM providers switchable via LLM_PROVIDER env var.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── LLM Provider ────────────────────────────────────────
    llm_provider: Literal["ollama", "anthropic", "openai", "groq", "gemini"] = "ollama"

    # ── Ollama ───────────────────────────────────────────────
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma4:27b"
    ollama_vision_model: str = "llava:34b"
    ollama_cloud_mode: bool = True

    # ── Anthropic ────────────────────────────────────────────
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-sonnet-20241022"

    # ── OpenAI ───────────────────────────────────────────────
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # ── Groq ─────────────────────────────────────────────────
    groq_api_key: str = ""
    groq_model: str = "meta-llama/llama-4-scout-17b-16e-instruct"

    # ── Google Gemini ────────────────────────────────────────
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    # ── Redis ────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379"
    redis_ttl: int = 86400

    # ── MongoDB ──────────────────────────────────────────────
    mongodb_url: str = "mongodb://localhost:27017"
    mongodb_db_name: str = "marketing_agent"

    # ── FastAPI ──────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8010
    api_secret_key: str = "dev-secret-key-change-in-production"
    cors_origins: str = "http://localhost:3000"

    # ── File Storage ─────────────────────────────────────────
    upload_dir: str = "./uploads"
    max_file_size_mb: int = 10

    # ── Agent Behavior ───────────────────────────────────────
    agent_max_iterations: int = 10
    agent_temperature: float = 0.7
    agent_stream: bool = True

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def active_text_model(self) -> str:
        """Return the model name for the currently active provider."""
        return {
            "ollama": self.ollama_model,
            "anthropic": self.anthropic_model,
            "openai": self.openai_model,
            "groq": self.groq_model,
            "gemini": self.gemini_model,
        }[self.llm_provider]

    @property
    def active_vision_model(self) -> str:
        """Return the vision-capable model for the active provider."""
        return {
            "ollama": self.ollama_vision_model,
            "anthropic": self.anthropic_model,       # Claude is multimodal
            "openai": self.openai_model,              # GPT-4o is multimodal
            "groq": self.groq_model,                  # Llama4 is multimodal
            "gemini": self.gemini_model,             # Gemini is multimodal
        }[self.llm_provider]


@lru_cache
def get_settings() -> Settings:
    return Settings()
