from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # LLM Provider
    MODEL_PROVIDER: str = "ollama"
    MODEL_NAME: str = "qwen3.5:397b-cloud"
    OLLAMA_BASE_URL: str = "http://localhost:11434"

    # API Keys
    OPENAI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    SERPER_API_KEY: Optional[str] = None
    TAVILY_API_KEY: Optional[str] = None

    # Database
    MONGODB_URI: str = "mongodb://localhost:27017"
    MONGODB_DB: str = "leadgen"

    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    # App
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    CORS_ORIGINS: str = "http://localhost:3000"

    # Agent
    MAX_ITERATIONS: int = 20

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
