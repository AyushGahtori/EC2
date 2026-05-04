"""
Company enrichment tool using Tavily API.
Extracts website, description, funding, industry, company size.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Optional

import httpx
from langchain_core.tools import tool

from app.config.settings import settings
from app.services.redis_client import cache_get, cache_set

logger = logging.getLogger(__name__)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
NON_COMPANY_DOMAINS = {
    "linkedin.com",
    "www.linkedin.com",
    "wikipedia.org",
    "www.wikipedia.org",
    "crunchbase.com",
    "www.crunchbase.com",
    "facebook.com",
    "www.facebook.com",
    "instagram.com",
    "www.instagram.com",
    "x.com",
    "www.x.com",
    "twitter.com",
    "www.twitter.com",
}
INDUSTRY_KEYWORDS = [
    ("FinTech", ["fintech", "payments", "banking", "financial services", "lending", "insurtech"]),
    ("SaaS", ["saas", "software-as-a-service", "software platform", "b2b software"]),
    ("AI / ML", ["artificial intelligence", "machine learning", "generative ai", "ai platform"]),
    ("E-commerce", ["e-commerce", "ecommerce", "online retail", "marketplace"]),
    ("Healthcare", ["healthcare", "health tech", "medtech", "biotech", "digital health"]),
    ("Cybersecurity", ["cybersecurity", "identity security", "threat detection", "security platform"]),
    ("Education", ["edtech", "education technology", "learning platform"]),
    ("Logistics", ["logistics", "supply chain", "freight", "shipping"]),
    ("Real Estate", ["proptech", "real estate", "property technology"]),
    ("Marketing", ["adtech", "marketing platform", "customer engagement"]),
]


async def _tavily_search(query: str, max_results: int = 5) -> dict:
    """Call Tavily search API."""
    if not settings.TAVILY_API_KEY:
        return {"results": [], "error": "TAVILY_API_KEY not configured"}

    cache_key = f"tavily:{hashlib.md5(query.encode()).hexdigest()}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    payload = {
        "api_key": settings.TAVILY_API_KEY,
        "query": query,
        "search_depth": "advanced",
        "max_results": max_results,
        "include_raw_content": False,
        "include_answer": True,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(TAVILY_SEARCH_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()

    await cache_set(cache_key, data, ttl=7200)
    return data


def _extract_company_size(text: str) -> str:
    """Extract employee count / company size from text."""
    patterns = [
        r"(\d[\d,]+)\s+employees?",
        r"team of (\d+)",
        r"(\d+)\+?\s+people",
        r"(startup|small|mid-?size[d]?|enterprise|large)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1)
    return ""


def _extract_funding(text: str) -> str:
    """Extract funding info from text."""
    m = re.search(
        r"(seed|series [a-e]|pre-seed|raised|funding)[^.]{0,80}(\$[\d.,]+[MBK]?)",
        text,
        re.IGNORECASE,
    )
    if m:
        return m.group(0)[:100]
    return ""


def _extract_domain(url: str) -> str:
    cleaned = re.sub(r"^https?://", "", (url or "").strip().lower())
    return cleaned.split("/")[0].removeprefix("www.")


def _looks_like_company_site(url: str, company_name: str) -> bool:
    domain = _extract_domain(url)
    if not domain:
        return False
    if domain in NON_COMPANY_DOMAINS:
        return False
    company_slug = re.sub(r"[^a-z0-9]", "", company_name.lower())
    return company_slug[:6] in re.sub(r"[^a-z0-9]", "", domain) or "." in domain


def _extract_industry(text: str) -> str:
    """Infer a practical industry label from enrichment text."""
    normalized = re.sub(r"\s+", " ", text or "")
    field_match = re.search(
        r"\bindustry[:\s]+([A-Za-z/&,\- ]{3,50})",
        normalized,
        re.IGNORECASE,
    )
    if field_match:
        return field_match.group(1).strip(" ,.-")

    lowered = normalized.lower()
    for industry, keywords in INDUSTRY_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return industry

    return ""


@tool
async def company_enrichment(company_name: str, website: str = "") -> str:
    """
    Enrich company data: website, description, industry, size, funding.
    Use for: getting detailed info about a company after finding the lead.

    Args:
        company_name: Full company name
        website: Company website URL (optional, speeds up enrichment)

    Returns:
        JSON with enriched company data
    """
    try:
        query = f"{company_name} company overview funding employees industry"
        if website:
            query += f" site:{website}"

        data = await _tavily_search(query)

        combined_text = ""
        company_website = website
        description = ""
        sources = []

        answer = data.get("answer", "")
        if answer:
            combined_text += answer + " "

        for r in data.get("results", []):
            combined_text += r.get("content", "") + " "
            sources.append(r.get("url", ""))
            if not company_website and "website" in r.get("title", "").lower():
                company_website = r.get("url", "")
            elif not company_website and _looks_like_company_site(r.get("url", ""), company_name):
                company_website = r.get("url", "")

        # Extract description (first 300 chars of meaningful content)
        if answer:
            description = answer[:300]
        elif combined_text:
            description = combined_text[:300]

        # Best guess at website
        if not company_website:
            for src in sources:
                if company_name.lower().replace(" ", "") in src.lower():
                    company_website = src
                    break

        result = {
            "company_name": company_name,
            "website": company_website,
            "description": description.strip(),
            "company_size": _extract_company_size(combined_text),
            "funding": _extract_funding(combined_text),
            "industry": _extract_industry(combined_text),
            "sources": sources[:3],
        }
        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        logger.error(f"company_enrichment error: {e}")
        return json.dumps({
            "company_name": company_name,
            "error": str(e),
            "website": website,
        })
