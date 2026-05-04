"""
LinkedIn search tool — uses Google (Serper) to search site:linkedin.com/in
This avoids LinkedIn rate limits while still surfacing profile data.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from urllib.parse import unquote

import httpx
from langchain_core.tools import tool

from app.config.settings import settings
from app.services.redis_client import cache_get, cache_set

logger = logging.getLogger(__name__)


TITLE_HINTS = (
    "ceo",
    "founder",
    "co-founder",
    "cto",
    "cfo",
    "coo",
    "chief",
    "president",
    "partner",
    "director",
    "vice president",
    "vp",
    "head",
    "manager",
    "lead",
    "owner",
    "principal",
    "consultant",
    "engineer",
)


def _looks_like_title(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in TITLE_HINTS)


def _clean_company_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip(" -|,")
    return re.sub(r"\s+(location|area)\b.*$", "", cleaned, flags=re.IGNORECASE).strip()


def _split_headline(headline: str) -> tuple[str, str]:
    """Split a LinkedIn headline into title and company when possible."""
    headline = re.sub(r"\s+", " ", headline).strip(" -")
    if not headline:
        return "", ""

    at_match = re.match(r"(?P<title>.+?)\s+at\s+(?P<company>.+)", headline, re.IGNORECASE)
    if at_match:
        return at_match.group("title").strip(), _clean_company_text(at_match.group("company"))

    dash_parts = [part.strip() for part in headline.split(" - ") if part.strip()]
    if len(dash_parts) >= 2 and _looks_like_title(dash_parts[0]):
        return dash_parts[0], _clean_company_text(" - ".join(dash_parts[1:]))

    pipe_parts = [part.strip() for part in headline.split(" | ") if part.strip()]
    if len(pipe_parts) >= 2 and _looks_like_title(pipe_parts[0]):
        return pipe_parts[0], _clean_company_text(pipe_parts[1])

    comma_parts = [part.strip() for part in headline.split(",") if part.strip()]
    if len(comma_parts) >= 2 and _looks_like_title(comma_parts[0]):
        return comma_parts[0], _clean_company_text(comma_parts[1])

    if _looks_like_title(headline):
        return headline, ""

    if len(headline.split()) <= 6:
        return "", _clean_company_text(headline)

    return headline, ""


def _parse_snippet_headline(snippet: str) -> tuple[str, str]:
    """Fallback extractor for cases where the SERP title lacks company/title detail."""
    normalized = re.sub(r"\s+", " ", snippet).strip()
    if not normalized:
        return "", ""

    at_match = re.search(
        r"([A-Za-z0-9/&(),.+\- ]{3,80}?)\s+at\s+([A-Z][A-Za-z0-9&'(),.+\- ]{2,80})",
        normalized,
        re.IGNORECASE,
    )
    if at_match:
        return at_match.group(1).strip(), _clean_company_text(at_match.group(2))

    comma_match = re.search(
        r"([A-Za-z0-9/&() .+\-]{3,80}),\s*([A-Z][A-Za-z0-9&'(),.+\- ]{2,80})",
        normalized,
    )
    if comma_match and _looks_like_title(comma_match.group(1)):
        return comma_match.group(1).strip(), _clean_company_text(comma_match.group(2))

    return "", ""


def _parse_linkedin_snippet(snippet: str, url: str, title: str) -> dict:
    """Extract structured data from LinkedIn SERP snippet."""
    name, title_text, company = "", "", ""

    title_clean = title.replace(" | LinkedIn", "").replace(" - LinkedIn", "")
    parts = [p.strip() for p in title_clean.split(" - ")]
    if len(parts) >= 1:
        name = parts[0]

    headline = " - ".join(parts[1:]) if len(parts) > 1 else ""
    title_text, company = _split_headline(headline)

    if not title_text or not company:
        snippet_title, snippet_company = _parse_snippet_headline(snippet)
        title_text = title_text or snippet_title
        company = company or snippet_company

    # Extract profile URL
    linkedin_url = url if "linkedin.com/in/" in url else ""

    # Extract location / other info from snippet
    location = ""
    loc_match = re.search(r"Location[:\s]+([^·\n]+)", snippet, re.IGNORECASE)
    if loc_match:
        location = loc_match.group(1).strip()

    return {
        "name": name,
        "title": title_text,
        "company": company,
        "linkedin_url": linkedin_url,
        "location": location,
        "snippet": snippet,
    }


@tool
async def linkedin_search(
    query: str,
    num_results: int = 10,
) -> str:
    """
    Search for LinkedIn profiles using Google search (site:linkedin.com/in).
    Use for: finding specific professionals, founders, executives.

    Args:
        query: Search terms such as "SaaS founder India" or "CTO fintech startup"
        num_results: Number of profiles to return

    Returns:
        JSON with list of LinkedIn profiles: name, title, company, linkedin_url
    """
    search_query = f'site:linkedin.com/in {query}'
    cache_key = f"linkedin:{hashlib.md5(search_query.encode()).hexdigest()}"
    cached = await cache_get(cache_key)
    if cached:
        return json.dumps(cached)

    if not settings.SERPER_API_KEY:
        return json.dumps({"error": "SERPER_API_KEY not configured", "profiles": []})

    try:
        url = "https://google.serper.dev/search"
        headers = {"X-API-KEY": settings.SERPER_API_KEY, "Content-Type": "application/json"}
        payload = {"q": search_query, "num": min(num_results, 10)}

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        profiles = []
        for item in data.get("organic", []):
            if "linkedin.com/in/" not in item.get("link", ""):
                continue
            profile = _parse_linkedin_snippet(
                snippet=item.get("snippet", ""),
                url=item.get("link", ""),
                title=item.get("title", ""),
            )
            if profile["name"]:
                profiles.append(profile)

        result = {
            "query": query,
            "profiles": profiles,
            "total_found": len(profiles),
        }
        await cache_set(cache_key, result, ttl=3600)
        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        logger.error(f"linkedin_search error: {e}")
        return json.dumps({"error": str(e), "profiles": []})
