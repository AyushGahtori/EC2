"""
Product routes — save and retrieve full product analysis for a session.
Called after image upload + analysis to persist structured product data.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from models.schemas import ProductDocument
from services.mongodb_service import get_product, save_product, get_session
from services.redis_service import cache_product_analysis, get_cached_product_analysis

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/products", tags=["products"])


class SaveProductRequest(BaseModel):
    session_id: str
    image_id: str
    image_url: str
    analysis: str
    name: str | None = None


@router.post("")
async def save_product_analysis(body: SaveProductRequest):
    """Persist a product analysis result to MongoDB + Redis cache."""
    session = await get_session(body.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    doc = ProductDocument(
        session_id=body.session_id,
        name=body.name,
        analysis=body.analysis,
        image_id=body.image_id,
        image_url=body.image_url,
    )
    product_id = await save_product(doc)

    # Cache in Redis for fast retrieval
    await cache_product_analysis(body.session_id, body.analysis)

    # Update session product name
    from services.mongodb_service import update_session
    if body.name:
        await update_session(body.session_id, {"product_name": body.name})

    return {"id": product_id, "session_id": body.session_id}


@router.get("/{session_id}")
async def get_product_analysis(session_id: str):
    """Get the product analysis for a session (cache-first)."""
    # Try Redis cache first
    cached = await get_cached_product_analysis(session_id)
    if cached:
        return {"analysis": cached, "source": "cache"}

    # Fallback to MongoDB
    product = await get_product(session_id)
    if not product:
        raise HTTPException(status_code=404, detail="No product analysed in this session yet")

    return {"analysis": product.get("analysis"), "source": "db", "product": product}
