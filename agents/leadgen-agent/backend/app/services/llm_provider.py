"""
Dynamic LLM provider — switch between Ollama, OpenAI, Anthropic via ENV.
No hardcoded model logic; provider is resolved at runtime.
"""
from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from app.config.settings import settings


def get_llm(streaming: bool = False) -> BaseChatModel:
    """
    Return the correct LangChain chat model based on MODEL_PROVIDER env var.
    Supports: ollama | openai | anthropic
    """
    provider = settings.MODEL_PROVIDER.lower().strip()

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.MODEL_NAME,
            api_key=settings.OPENAI_API_KEY,
            streaming=streaming,
            temperature=0,
        )

    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=settings.MODEL_NAME,
            api_key=settings.ANTHROPIC_API_KEY,
            streaming=streaming,
            temperature=0,
        )

    elif provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=settings.MODEL_NAME,
            base_url=settings.OLLAMA_BASE_URL,
            temperature=0,
        )

    else:
        raise ValueError(
            f"Unsupported MODEL_PROVIDER='{provider}'. "
            "Choose from: ollama | openai | anthropic"
        )
