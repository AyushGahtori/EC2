"""
Runtime configuration routes — list available LLM providers and switch between them.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.runtime_config import (
    ALLOWED_PROVIDERS,
    active_model_for,
    get_active_provider,
    provider_is_configured,
    set_active_provider,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/config", tags=["config"])


class ProviderInfo(BaseModel):
    name: str
    configured: bool
    model: str


class ProvidersResponse(BaseModel):
    active: str
    providers: list[ProviderInfo]


class SwitchProviderRequest(BaseModel):
    provider: str


@router.get("/providers", response_model=ProvidersResponse)
async def list_providers() -> ProvidersResponse:
    """Return every supported provider, which ones have credentials configured,
    the model each is set to, and which one is currently active."""
    return ProvidersResponse(
        active=get_active_provider(),
        providers=[
            ProviderInfo(
                name=name,
                configured=provider_is_configured(name),
                model=active_model_for(name),
            )
            for name in sorted(ALLOWED_PROVIDERS)
        ],
    )


@router.post("/provider", response_model=ProvidersResponse)
async def switch_provider(body: SwitchProviderRequest) -> ProvidersResponse:
    """Switch the active LLM provider at runtime.
    Clears cached LLM clients and forces the agent graph to recompile on the
    next request with the new LLM binding."""
    target = body.provider.strip().lower()
    if target not in ALLOWED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider {body.provider!r}. Choose from {sorted(ALLOWED_PROVIDERS)}",
        )
    if not provider_is_configured(target):
        raise HTTPException(
            status_code=400,
            detail=f"Provider {target!r} has no credentials configured. Set its API key in .env and restart.",
        )

    set_active_provider(target)

    # Invalidate caches that baked in the old provider.
    from services.llm_service import clear_llm_cache
    from agents.marketing_agent import reset_agent_graph

    clear_llm_cache()
    reset_agent_graph()

    logger.info(f"LLM provider switched to: {target}")
    return await list_providers()
