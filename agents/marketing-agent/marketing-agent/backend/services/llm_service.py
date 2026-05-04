"""
LLM Service — Multi-provider abstraction.

Supports: Ollama (default), Anthropic, OpenAI, Groq
Switching between providers is controlled entirely by the LLM_PROVIDER env var.
All providers expose the same interface so the agent code never changes.
"""
from __future__ import annotations

import base64
import logging
from functools import lru_cache
from typing import AsyncGenerator, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from config.settings import get_settings

logger = logging.getLogger(__name__)


def _build_ollama_llm(vision: bool = False) -> BaseChatModel:
    """Build Ollama chat model (local or cloud remote endpoint)."""
    from langchain_ollama import ChatOllama

    settings = get_settings()
    model = settings.ollama_vision_model if vision else settings.ollama_model

    return ChatOllama(
        model=model,
        base_url=settings.ollama_base_url,
        temperature=settings.agent_temperature,
        num_predict=4096,
        # Stream by default; LangGraph handles the streaming loop
    )


def _build_anthropic_llm() -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic

    settings = get_settings()
    return ChatAnthropic(
        model=settings.anthropic_model,
        api_key=settings.anthropic_api_key,
        temperature=settings.agent_temperature,
        max_tokens=4096,
    )


def _build_openai_llm() -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    settings = get_settings()
    return ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        temperature=settings.agent_temperature,
        max_tokens=4096,
    )


def _build_groq_llm() -> BaseChatModel:
    from langchain_groq import ChatGroq

    settings = get_settings()
    return ChatGroq(
        model=settings.groq_model,
        api_key=settings.groq_api_key,
        temperature=settings.agent_temperature,
        max_tokens=8192,
    )


def _build_gemini_llm() -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI

    settings = get_settings()
    return ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.gemini_api_key,
        temperature=settings.agent_temperature,
        max_output_tokens=8192,
    )


@lru_cache(maxsize=16)
def _build_llm(provider: str, vision: bool) -> BaseChatModel:
    """Cache key is (provider, vision) so switching provider builds a new client
    without cross-contaminating previously-active ones."""
    logger.info(f"Initialising LLM | provider={provider} | vision={vision}")

    builders = {
        "ollama": lambda: _build_ollama_llm(vision=vision),
        "anthropic": _build_anthropic_llm,
        "openai": _build_openai_llm,
        "groq": _build_groq_llm,
        "gemini": _build_gemini_llm,
    }
    if provider not in builders:
        raise ValueError(f"Unknown LLM provider: {provider!r}. Choose from {list(builders)}")
    return builders[provider]()


def get_llm(vision: bool = False) -> BaseChatModel:
    """Return the LLM instance for the currently-active provider (runtime-overridable)."""
    from services.runtime_config import get_active_provider
    return _build_llm(get_active_provider(), vision)


def clear_llm_cache() -> None:
    """Drop cached LLM clients so the next get_llm() rebuilds from current config."""
    _build_llm.cache_clear()


def build_vision_message(
    text: str,
    image_b64: Optional[str] = None,
    image_url: Optional[str] = None,
) -> HumanMessage:
    """
    Build a HumanMessage that includes an image payload.
    Works across all supported providers — LangChain normalises the format.
    """
    if image_b64:
        # Detect image type from magic bytes
        header = base64.b64decode(image_b64[:16] + "==")
        if header[:4] == b"\x89PNG":
            media_type = "image/png"
        elif header[:3] == b"GIF":
            media_type = "image/gif"
        elif header[:4] == b"RIFF":
            media_type = "image/webp"
        else:
            media_type = "image/jpeg"

        return HumanMessage(content=[
            {
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{image_b64}"},
            },
            {"type": "text", "text": text},
        ])

    if image_url:
        return HumanMessage(content=[
            {"type": "image_url", "image_url": {"url": image_url}},
            {"type": "text", "text": text},
        ])

    return HumanMessage(content=text)


async def quick_completion(
    system_prompt: str,
    user_message: str,
    image_b64: Optional[str] = None,
) -> str:
    """
    One-shot completion helper for tools that need a quick LLM call.
    Uses vision model if image_b64 is provided.
    """
    llm = get_llm(vision=bool(image_b64))
    messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]

    if image_b64:
        messages.append(build_vision_message(user_message, image_b64=image_b64))
    else:
        messages.append(HumanMessage(content=user_message))

    response = await llm.ainvoke(messages)
    return str(response.content)
