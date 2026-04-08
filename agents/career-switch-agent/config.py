"""
config.py — Central configuration for the Career Transition Agent backend.
All secrets and environment-specific values are loaded from .env
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------- Firebase / Firestore -------
    FIREBASE_SERVICE_ACCOUNT_KEY: str = "./serviceAccountKey.json"
    FIRESTORE_ROLES_COLLECTION: str = "onet_roles"

    # ------- JSearch API -------
    JSEARCH_API_KEY: str = ""
    JSEARCH_RAPIDAPI_KEY: str = ""
    JSEARCH_API_HOST: str = "jsearch.p.rapidapi.com"
    RAPIDAPI_KEY: str = ""
    RAPIDAPI_HOST: str = ""
    JSEARCH_COUNTRY: str = "us"
    JSEARCH_PAGE: int = 1
    JSEARCH_MAX_RESULTS: int = 10

    # ------- Adzuna API -------
    ADZUNA_APP_ID: str = ""
    ADZUNA_APP_KEY: str = ""
    ADZUNA_COUNTRY: str = "us"
    ADZUNA_MAX_RESULTS: int = 10

    # ------- LinkedIn Jobs API -------
    LINKEDIN_API_KEY: str = ""
    LINKEDIN_API_HOST: str = ""
    LINKEDIN_API_BASE_URL: str = ""
    LINKEDIN_Client_ID: str = ""
    LINKEDIN_Primary_Client_Secret: str = ""
    LINKEDIN_COUNTRY: str = "us"
    LINKEDIN_PAGE: int = 1
    LINKEDIN_MAX_RESULTS: int = 10

    # ------- YouTube API -------
    YOUTUBE_API_KEY: str = ""
    YOUTUBE_MAX_RESULTS_PER_PHASE: int = 2

    # ------- Google Gemini LLM -------
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GEMINI_MODEL_PRO: str = "gemini-2.5-pro"
    GEMINI_MODEL_FLASH: str = "gemini-2.5-flash"
    GEMINI_MODEL_FLASH_LITE: str = "gemini-2.5-flash-lite"

    # ------- App -------
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"

@lru_cache()
def get_settings() -> Settings:
    return Settings()
