from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from app.core.config import Settings


class ProviderConfigurationError(RuntimeError):
    pass


def _require(value: str | None, env_name: str, provider: str) -> str:
    if not value:
        raise ProviderConfigurationError(
            f"`{env_name}` is required when LLM_PROVIDER={provider}."
        )
    return value


def build_chat_model(settings: Settings) -> BaseChatModel:
    provider = settings.LLM_PROVIDER
    temperature = settings.LLM_TEMPERATURE

    if provider == "ollama":
        return ChatOllama(
            model=settings.OLLAMA_MODEL,
            base_url=settings.OLLAMA_BASE_URL,
            temperature=temperature,
            num_predict=settings.LLM_MAX_TOKENS,
        )

    if provider == "ollama_cloud":
        api_key = _require(
            settings.OLLAMA_CLOUD_API_KEY, "OLLAMA_CLOUD_API_KEY", "ollama_cloud"
        )
        return ChatOpenAI(
            model=settings.OLLAMA_CLOUD_MODEL,
            api_key=api_key,
            base_url=settings.OLLAMA_CLOUD_BASE_URL,
            temperature=temperature,
            max_tokens=settings.LLM_MAX_TOKENS,
        )

    if provider == "openai":
        api_key = _require(settings.OPENAI_API_KEY, "OPENAI_API_KEY", "openai")
        return ChatOpenAI(
            model=settings.OPENAI_MODEL,
            api_key=api_key,
            temperature=temperature,
            max_tokens=settings.LLM_MAX_TOKENS,
        )

    if provider == "anthropic":
        api_key = _require(
            settings.ANTHROPIC_API_KEY, "ANTHROPIC_API_KEY", "anthropic"
        )
        return ChatAnthropic(
            model=settings.ANTHROPIC_MODEL,
            api_key=api_key,
            temperature=temperature,
            max_tokens=settings.LLM_MAX_TOKENS,
        )

    if provider == "gemini":
        api_key = _require(settings.GEMINI_API_KEY, "GEMINI_API_KEY", "gemini")
        return ChatGoogleGenerativeAI(
            model=settings.GEMINI_MODEL,
            google_api_key=api_key,
            temperature=temperature,
            max_output_tokens=settings.LLM_MAX_TOKENS,
        )

    raise ProviderConfigurationError(f"Unsupported LLM_PROVIDER: {provider}")
