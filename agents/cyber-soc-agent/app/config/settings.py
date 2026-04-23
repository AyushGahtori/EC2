from functools import lru_cache
import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    virustotal_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma4:31b-cloud"
    ollama_timeout: int = 120
    app_name: str = "Cyber AI SOC"
    debug: bool = False
    max_log_limit: int = 200

    class Config:
        env_file = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", ".env")
        )


@lru_cache()
def get_settings() -> Settings:
    return Settings()
