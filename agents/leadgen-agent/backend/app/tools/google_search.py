"""
Google Search tool using Serper API.
Handles general web search and news search.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

import httpx
from langchain_core.tools import tool

from app.config.settings import settings
from app.services.redis_client import cache_get, cache_set

logger = logging.getLogger(__name__)

SERPER_SEARCH_URL = "https://google.serper.dev/search"


async def _serper_request(query: str, search_type: str = "search", num: int = 10) -> dict:
    """Make a cached request to Serper API."""
    cache_key = f"serper:{hashlib.md5(f'{search_type}:{query}'.encode()).hexdigest()}"
    cached = await cache_get(cache_key)
    if cached:
        logger.info(f"Cache HIT for query: {query}")
        return cached

    if not settings.SERPER_API_KEY:
        logger.warning("SERPER_API_KEY not set — returning mock data")
        return {"organic": [], "error": "SERPER_API_KEY not configured"}

    url = f"https://google.serper.dev/{search_type}"
    headers = {"X-API-KEY": settings.SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {"q": query, "num": num}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    await cache_set(cache_key, data, ttl=3600)
    return data


def _parse_organic(data: dict) -> list[dict]:
    results = []
    for item in data.get("organic", []):
        results.append({
            "title": item.get("title", ""),
            "url": item.get("link", ""),
            "snippet": item.get("snippet", ""),
            "position": item.get("position", 0),
        })
    return results


@tool
async def google_search(query: str, num_results: int = 10) -> str:
    """
    Search the web using Google (via Serper API).
    Use for: finding people, companies, news, any general information.

    Args:
        query: The search query string
        num_results: Number of results to return (max 10)

    Returns:
        JSON string with search results including title, url, snippet
    """
    try:
        data = await _serper_request(query, search_type="search", num=min(num_results, 10))
        results = _parse_organic(data)

        # Also include knowledge graph if available
        kg = data.get("knowledgeGraph", {})
        people_also_ask = data.get("peopleAlsoAsk", [])

        output = {
            "query": query,
            "results": results,
            "knowledge_graph": {
                "title": kg.get("title"),
                "type": kg.get("type"),
                "description": kg.get("description"),
                "website": kg.get("website"),
            } if kg else None,
            "related_questions": [q.get("question") for q in people_also_ask[:3]],
            "total_found": len(results),
        }
        return json.dumps(output, ensure_ascii=False)
    except Exception as e:
        logger.error(f"google_search error: {e}")
        return json.dumps({"error": str(e), "results": []})
