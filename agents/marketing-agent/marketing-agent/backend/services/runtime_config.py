"""
Runtime-overridable LLM provider configuration.

The .env-loaded `settings.llm_provider` is the boot default. Once the app is
running, the UI can switch providers on the fly via POST /api/config/provider.
This module holds the mutable override and exposes `get_active_provider()` so
call-sites (llm_service, agent graph) resolve the current one at call time.
"""
from __future__ import annotations

from typing import Optional

from config.settings import get_settings

# None → use settings.llm_provider; otherwise, use this override.
_override: Optional[str] = None

ALLOWED_PROVIDERS = {"ollama", "anthropic", "openai", "groq", "gemini"}


def get_active_provider() -> str:
    if _override is not None:
        return _override
    chosen = get_settings().llm_provider
    # If the .env-selected provider has no credentials, fall back to the first
    # one that does — avoids boot-time "Connection error" when someone switches
    # provider in .env without adding its key.
    if provider_is_configured(chosen):
        return chosen
    for candidate in ("groq", "gemini", "openai", "anthropic", "ollama"):
        if candidate != chosen and provider_is_configured(candidate):
            return candidate
    return chosen


def resolve_boot_provider() -> tuple[str, str | None]:
    """Return (active_provider, warning) — warning is set when we had to fall
    back because the env-chosen provider lacks credentials."""
    chosen = get_settings().llm_provider
    if provider_is_configured(chosen):
        return chosen, None
    active = get_active_provider()
    if active != chosen:
        return active, (
            f"LLM_PROVIDER={chosen!r} has no API key configured. "
            f"Falling back to {active!r}. Set the key in .env and restart "
            f"to use {chosen!r}."
        )
    return chosen, f"No LLM provider is configured. Set an API key in .env."


def set_active_provider(provider: str) -> str:
    """Switch the active provider at runtime and return the new value."""
    global _override
    p = provider.strip().lower()
    if p not in ALLOWED_PROVIDERS:
        raise ValueError(f"Unknown provider: {provider!r}. Choose from {sorted(ALLOWED_PROVIDERS)}")
    _override = p
    return p


def provider_is_configured(provider: str) -> bool:
    """Whether the given provider has an API key / endpoint configured."""
    s = get_settings()
    return {
        "ollama": bool(s.ollama_base_url),
        "anthropic": bool(s.anthropic_api_key) and not s.anthropic_api_key.endswith("..."),
        "openai": bool(s.openai_api_key) and not s.openai_api_key.endswith("..."),
        "groq": bool(s.groq_api_key) and not s.groq_api_key.endswith("..."),
        "gemini": bool(s.gemini_api_key) and not s.gemini_api_key.endswith("..."),
    }.get(provider, False)


def active_model_for(provider: str, vision: bool = False) -> str:
    """The model name currently configured for a given provider."""
    s = get_settings()
    if provider == "ollama":
        return s.ollama_vision_model if vision else s.ollama_model
    return {
        "anthropic": s.anthropic_model,
        "openai": s.openai_model,
        "groq": s.groq_model,
        "gemini": s.gemini_model,
    }.get(provider, "")
