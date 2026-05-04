"""
Email finder tool — scrapes company pages and uses pattern matching.
Tries: website contact page, common patterns, Google search.
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

# Email pattern
EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)
PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s().\-]*)?(?:\d[\s().\-]*){7,14}\d"
)

# Pages likely to have contact info
CONTACT_PATHS = [
    "/contact",
    "/contact-us",
    "/about",
    "/team",
    "/about-us",
    "/people",
]


async def _fetch_page(url: str, timeout: int = 15) -> str:
    """Fetch a page and return its text content."""
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; LeadBot/1.0)"},
        ) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.text
    except Exception as e:
        logger.debug(f"_fetch_page failed for {url}: {e}")
    return ""


def _extract_emails(text: str) -> list[str]:
    """Extract unique, valid-looking emails from text."""
    found = EMAIL_RE.findall(text)
    # Filter out common false positives
    filtered = []
    for e in found:
        if any(bad in e for bad in ["example", "yourname", "email@", "test@", ".png", ".jpg"]):
            continue
        filtered.append(e.lower())
    return list(dict.fromkeys(filtered))  # dedupe preserving order


def _extract_phone_numbers(text: str) -> list[str]:
    """Extract unique phone numbers from arbitrary text."""
    found = []
    for match in PHONE_RE.findall(text or ""):
        digits = re.sub(r"\D", "", match)
        if len(digits) < 7 or len(digits) > 15:
            continue
        found.append(re.sub(r"\s+", " ", match).strip())
    return list(dict.fromkeys(found))


def _guess_email_patterns(first_name: str, last_name: str, domain: str) -> list[str]:
    """Generate common corporate email patterns."""
    fn = first_name.lower().strip()
    ln = last_name.lower().strip()
    if not fn or not ln or not domain:
        return []
    return [
        f"{fn}@{domain}",
        f"{fn}.{ln}@{domain}",
        f"{fn[0]}{ln}@{domain}",
        f"{fn}{ln[0]}@{domain}",
        f"{ln}@{domain}",
        f"contact@{domain}",
        f"info@{domain}",
    ]


def _extract_domain(website: str) -> str:
    """Extract bare domain from URL."""
    website = website.strip().lower()
    website = re.sub(r"https?://", "", website)
    website = website.split("/")[0]
    return website


@tool
async def email_finder(
    person_name: str,
    company_name: str = "",
    website: str = "",
) -> str:
    """
    Find email address for a person at a company.
    Tries website scraping, contact pages, and pattern generation.

    Args:
        person_name: Full name of the person (e.g., "John Smith")
        company_name: Company name
        website: Company website URL

    Returns:
        JSON with found emails and confidence score
    """
    cache_key = f"email:{hashlib.md5(f'{person_name}:{website}'.encode()).hexdigest()}"
    cached = await cache_get(cache_key)
    if cached:
        return json.dumps(cached)

    emails_found: list[str] = []
    phones_found: list[str] = []
    source = ""

    try:
        domain = _extract_domain(website) if website else ""

        # 1. Scrape known contact/about pages
        if website:
            base = website.rstrip("/")
            if not base.startswith("http"):
                base = "https://" + base

            for path in CONTACT_PATHS:
                text = await _fetch_page(base + path)
                if text:
                    found = _extract_emails(text)
                    phones = _extract_phone_numbers(text)
                    if found:
                        emails_found.extend(found)
                    if phones:
                        phones_found.extend(phones)
                    if found or phones:
                        source = f"{base}{path}"
                    if found:
                        break

        # 2. Scrape homepage if no emails yet
        if not emails_found and website:
            base = website.rstrip("/")
            if not base.startswith("http"):
                base = "https://" + base
            text = await _fetch_page(base)
            found = _extract_emails(text)
            phones = _extract_phone_numbers(text)
            emails_found.extend(found)
            phones_found.extend(phones)
            if found:
                source = base
            elif phones and not source:
                source = base

        # 3. Use Serper to search for email
        if not emails_found and settings.SERPER_API_KEY:
            query = f'"{person_name}" "{company_name}" email contact'
            import httpx as _httpx
            headers = {"X-API-KEY": settings.SERPER_API_KEY, "Content-Type": "application/json"}
            async with _httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    "https://google.serper.dev/search",
                    headers=headers,
                    json={"q": query, "num": 5},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get("organic", []):
                        found = _extract_emails(item.get("snippet", ""))
                        phones = _extract_phone_numbers(item.get("snippet", ""))
                        emails_found.extend(found)
                        phones_found.extend(phones)
                    if emails_found:
                        source = "google_search"
                    elif phones_found and not source:
                        source = "google_search"

        # 4. Generate pattern guesses as fallback
        name_parts = person_name.strip().split()
        patterns: list[str] = []
        if len(name_parts) >= 2 and domain:
            patterns = _guess_email_patterns(name_parts[0], name_parts[-1], domain)

        # Deduplicate
        emails_found = list(dict.fromkeys(emails_found))
        phones_found = list(dict.fromkeys(phones_found))

        # Score confidence
        confidence = 0.9 if emails_found else (0.5 if phones_found else (0.3 if patterns else 0.0))
        if source == "google_search":
            confidence = 0.6

        result = {
            "person_name": person_name,
            "company": company_name,
            "emails_found": emails_found[:3],
            "best_email": emails_found[0] if emails_found else None,
            "phone_numbers": phones_found[:3],
            "best_phone": phones_found[0] if phones_found else None,
            "email_patterns": patterns[:3] if not emails_found else [],
            "confidence": confidence,
            "source": source,
        }

        await cache_set(cache_key, result, ttl=86400)
        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        logger.error(f"email_finder error: {e}")
        return json.dumps({
            "person_name": person_name,
            "error": str(e),
            "emails_found": [],
            "best_email": None,
            "phone_numbers": [],
            "best_phone": None,
            "confidence": 0.0,
        })
