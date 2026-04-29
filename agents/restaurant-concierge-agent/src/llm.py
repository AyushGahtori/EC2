from __future__ import annotations

from langchain_google_genai import ChatGoogleGenerativeAI

from config import Config


def create_restaurant_llm(*, temperature: float | None = None) -> ChatGoogleGenerativeAI:
    api_key = Config.GOOGLE_API_KEY
    if not api_key:
        raise ValueError("GEMINI_API_KEY is required for restaurant-concierge-agent.")

    return ChatGoogleGenerativeAI(
        model=Config.MODEL_NAME,
        temperature=Config.MODEL_TEMPERATURE if temperature is None else temperature,
        google_api_key=api_key,
    )
