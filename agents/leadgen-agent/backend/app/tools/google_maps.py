"""
Google Maps / Places tool using Serper Maps API.
Used for finding businesses by location.
"""
from __future__ import annotations

import hashlib
import json
import logging

import httpx
from langchain_core.tools import tool

from app.config.settings import settings
from app.services.redis_client import cache_get, cache_set

logger = logging.getLogger(__name__)


@tool
async def google_maps(query: str, location: str = "") -> str:
    """
    Find businesses and companies on Google Maps via Serper.
    Use for: finding local businesses, offices, company locations.

    Args:
        query: Business type or company name to search for
        location: City, country or region (e.g., "Mumbai, India")

    Returns:
        JSON with places including name, address, rating, website, phone
    """
    full_query = f"{query} {location}".strip()
    cache_key = f"maps:{hashlib.md5(full_query.encode()).hexdigest()}"
    cached = await cache_get(cache_key)
    if cached:
        return json.dumps(cached)

    if not settings.SERPER_API_KEY:
        return json.dumps({"error": "SERPER_API_KEY not configured", "places": []})

    try:
        url = "https://google.serper.dev/maps"
        headers = {"X-API-KEY": settings.SERPER_API_KEY, "Content-Type": "application/json"}
        payload = {"q": full_query}

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        places = []
        for place in data.get("places", []):
            places.append({
                "name": place.get("title", ""),
                "address": place.get("address", ""),
                "phone": place.get("phoneNumber", ""),
                "website": place.get("website", ""),
                "rating": place.get("rating"),
                "reviews": place.get("ratingCount"),
                "category": place.get("category", ""),
                "hours": place.get("openingHours", ""),
            })

        result = {
            "query": full_query,
            "places": places,
            "total_found": len(places),
        }
        await cache_set(cache_key, result, ttl=3600)
        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        logger.error(f"google_maps error: {e}")
        return json.dumps({"error": str(e), "places": []})
