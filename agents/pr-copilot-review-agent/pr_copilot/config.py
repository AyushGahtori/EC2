"""
config.py - Central configuration for PR Copilot.
All environment-sensitive values live here.
"""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


load_dotenv()


def _required_env(name: str) -> str:
    """Read a required env var and raise a clear error when missing."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Set it in your environment or .env file."
        )
    return value


@dataclass
class Config:
    # GitHub
    github_token: str = field(
        default_factory=lambda: _required_env("GITHUB_TOKEN")
    )
    github_api_base: str = "https://api.github.com"

    # Ollama
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen3-coder:480b-cloud")
    ollama_timeout: int = int(os.getenv("OLLAMA_TIMEOUT", "120"))

    # Chunking
    diff_chunk_threshold: int = int(os.getenv("DIFF_CHUNK_THRESHOLD", "400"))  # lines
    diff_chunk_size: int = int(os.getenv("DIFF_CHUNK_SIZE", "150"))            # lines per chunk

    # Retry
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "2"))

    # Tool paths
    bandit_path: str = os.getenv("BANDIT_PATH", "bandit")
    flake8_path: str = os.getenv("FLAKE8_PATH", "flake8")

    # Webhook
    webhook_secret: str = os.getenv("WEBHOOK_SECRET", "")
    webhook_port: int = int(os.getenv("PORT", "8000"))

    # Repo guidelines file (optional)
    guidelines_file: str = os.getenv("GUIDELINES_FILE", "CONTRIBUTING.md")


# Singleton
settings = Config()
