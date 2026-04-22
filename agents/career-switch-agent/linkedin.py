"""
linkedin.py - LinkedIn job market data service.

This module is intentionally config-driven because LinkedIn job APIs are often
provider-specific. Configure the endpoint and host in .env, and this adapter
will normalize the response into JobListing objects.
"""

import logging
from typing import Any, Iterable, List

import httpx

from config import get_settings
from models import JobListing

logger = logging.getLogger(__name__)
settings = get_settings()


async def fetch_job_listings(target_role: str) -> List[JobListing]:
    """
    Fetch LinkedIn job listings using a configured provider endpoint.

    Required env vars:
    - LINKEDIN_Client_ID
    - LINKEDIN_Primary_Client_Secret
    Optional:
    - LINKEDIN_API_HOST
    - LINKEDIN_COUNTRY
    - LINKEDIN_PAGE
    - LINKEDIN_MAX_RESULTS
    """
    linkedin_token = _resolve_linkedin_api_key()
    api_host = _resolve_linkedin_api_host()
    api_base_url = _resolve_linkedin_api_base_url()

    if not linkedin_token:
        logger.warning("⚠️  LinkedIn API key not set. Returning empty job list.")
        return []

    if not api_base_url:
        logger.warning("⚠️  LinkedIn API base URL not set. Returning empty job list.")
        return []

    headers = {
        "Authorization": f"Bearer {linkedin_token}",
        "Content-Type": "application/json",
    }
    if api_host:
        headers["X-RapidAPI-Host"] = api_host
        headers["X-RapidAPI-Key"] = linkedin_token

    params = {
        "query": target_role,
        "keywords": target_role,
        "search": target_role,
        "page": settings.LINKEDIN_PAGE,
        "limit": settings.LINKEDIN_MAX_RESULTS,
        "country": settings.LINKEDIN_COUNTRY,
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(api_base_url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()

        results = _extract_results(data)[: settings.LINKEDIN_MAX_RESULTS]
        logger.info(f"✅ LinkedIn returned {len(results)} listings for '{target_role}'")

        return [_normalize_job(job) for job in results]
    except httpx.HTTPStatusError as e:
        logger.error(f"❌ LinkedIn API HTTP error: {e.response.status_code} - {e.response.text[:200]}")
        return []
    except httpx.RequestError as e:
        logger.error(f"❌ LinkedIn API request failed: {e}")
        return []
    except Exception as e:
        logger.error(f"❌ Unexpected error fetching LinkedIn data: {e}")
        return []


def _extract_results(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]

    if not isinstance(data, dict):
        return []

    for key in ("data", "jobs", "results", "items"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    return []


def _normalize_job(job: dict) -> JobListing:
    company = (
        job.get("company")
        or job.get("company_name")
        or job.get("organization")
        or _from_nested(job, "company", "name")
        or "Unknown Company"
    )
    location = (
        job.get("location")
        or job.get("job_location")
        or job.get("formatted_location")
        or _join_parts(_iter_values(job, "city", "state", "country"))
        or "Remote/Unspecified"
    )
    description = (
        job.get("description")
        or job.get("job_description")
        or job.get("snippet")
        or job.get("summary")
        or ""
    )
    redirect_url = (
        job.get("job_url")
        or job.get("redirect_url")
        or job.get("apply_url")
        or job.get("url")
    )

    return JobListing(
        title=job.get("title") or job.get("job_title") or "Unknown Title",
        company=str(company),
        location=str(location),
        description=_truncate(str(description), max_chars=500),
        redirect_url=redirect_url,
        source="LinkedIn",
    )


def _from_nested(data: dict, *keys: str) -> str | None:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return str(current) if current else None


def _iter_values(data: dict, *keys: str) -> Iterable[str]:
    for key in keys:
        value = data.get(key)
        if value:
            yield str(value)


def _join_parts(parts: Iterable[str]) -> str:
    values = [part for part in parts if part]
    return ", ".join(values)


def _truncate(text: str, max_chars: int) -> str:
    return text[:max_chars].strip() + ("..." if len(text) > max_chars else "")


def _resolve_linkedin_api_key() -> str:
    # Accept both naming styles and trim accidental spaces from .env values.
    api_key = (
        getattr(settings, "LINKEDIN_API_KEY", "")
        or getattr(settings, "LINKEDIN_Primary_Client_Secret", "")
        or getattr(settings, "LINKEDIN_Client_ID", "")
    )
    return str(api_key).strip()


def _resolve_linkedin_api_host() -> str:
    host = (
        getattr(settings, "LINKEDIN_API_HOST", "")
        or getattr(settings, "RAPIDAPI_HOST", "")
    )
    return str(host).strip()


def _resolve_linkedin_api_base_url() -> str:
    return str(getattr(settings, "LINKEDIN_API_BASE_URL", "")).strip()
