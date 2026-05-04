"""
LLM Service — supports Ollama, OpenAI, Anthropic, Gemini
Switched via LLM_PROVIDER env variable.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from loguru import logger

load_dotenv()


def get_llm(streaming: bool = False, temperature: float = 0.7) -> BaseChatModel:
    provider = os.getenv("LLM_PROVIDER", "ollama").lower()
    model = os.getenv("LLM_MODEL", "gemma3:4b")

    logger.info(f"Initialising LLM | provider={provider} model={model}")

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model or "gpt-4o-mini",
            api_key=os.getenv("OPENAI_API_KEY"),
            streaming=streaming,
            temperature=temperature,
        )

    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=model or "claude-sonnet-4-5",
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            streaming=streaming,
            temperature=temperature,
        )

    elif provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=model or "gemini-2.0-flash",
            google_api_key=os.getenv("GOOGLE_API_KEY"),
            streaming=streaming,
            temperature=temperature,
        )

    else:  # default: ollama
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=model,
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            streaming=streaming,
            temperature=temperature,
        )


@lru_cache(maxsize=4)
def get_cached_llm(streaming: bool = False) -> BaseChatModel:
    """Cached LLM instance (reused across requests)."""
    return get_llm(streaming=streaming)
