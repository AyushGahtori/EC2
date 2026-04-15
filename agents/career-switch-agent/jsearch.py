"""
jsearch.py — JSearch Job Market Data Service.

Responsibilities:
- Call JSearch for real-time job listings
- Parse and normalize the response into JobListing objects
- Gracefully fall back to an empty list on failure
"""

import logging
from typing import List
from urllib.parse import urlparse

import httpx

from config import get_settings
from models import JobListing

logger = logging.getLogger(__name__)
settings = get_settings()

JSEARCH_RAPIDAPI_BASE_URL = "https://jsearch.p.rapidapi.com/search"
JSEARCH_OPENWEBNINJA_BASE_URL = "https://api.openwebninja.com/jsearch/search"


async def fetch_job_listings(target_role: str) -> List[JobListing]:
    """
    Fetch live job listings from JSearch for a given role.
    """
    api_key = _resolve_api_key()
    api_host = _resolve_api_host()
    if not api_key:
        logger.warning("⚠️  JSearch API key not set. Returning empty job list.")
        return []

    params = {
        "query": target_role,
        "page": settings.JSEARCH_PAGE,
        "num_pages": 1,
        "country": settings.JSEARCH_COUNTRY,
        "date_posted": "all",
    }
    candidates = _candidate_requests(api_key=api_key, api_host=api_host)

    async with httpx.AsyncClient(timeout=20.0) as client:
        for endpoint, headers, label in candidates:
            try:
                response = await client.get(endpoint, params=params, headers=headers)
                response.raise_for_status()
                data = response.json()
                results = data.get("data", [])[: settings.JSEARCH_MAX_RESULTS]
                logger.info(f"✅ JSearch returned {len(results)} listings for '{target_role}' via {label}")
                return _normalize_listings(results)
            except httpx.HTTPStatusError as e:
                logger.warning(
                    f"⚠️  JSearch attempt failed via {label}: {e.response.status_code} — {e.response.text[:160]}"
                )
            except httpx.RequestError as e:
                logger.warning(f"⚠️  JSearch request error via {label}: {e}")
            except Exception as e:
                logger.warning(f"⚠️  JSearch unexpected error via {label}: {e}")

    logger.error("❌ JSearch failed across all endpoint/auth combinations. Returning empty job list.")
    return []


def _first_highlight(job: dict, key: str) -> str:
    highlights = job.get("job_highlights", {}).get(key, [])
    if highlights:
        return highlights[0]
    return ""


def _truncate(text: str, max_chars: int) -> str:
    return text[:max_chars].strip() + ("..." if len(text) > max_chars else "")


def _resolve_api_key() -> str:
    return (
        (settings.JSEARCH_API_KEY or "").strip()
        or (settings.JSEARCH_RAPIDAPI_KEY or "").strip()
        or (settings.RAPIDAPI_KEY or "").strip()
    )


def _resolve_api_host() -> str:
    return (
        (settings.JSEARCH_API_HOST or "").strip()
        or (settings.RAPIDAPI_HOST or "").strip()
        or "jsearch.p.rapidapi.com"
    )


def _resolve_endpoint_and_mode(api_host: str) -> tuple[str, str]:
    host_value = (api_host or "").strip()

    if host_value.startswith("http://") or host_value.startswith("https://"):
        parsed = urlparse(host_value)
        if "openwebninja.com" in parsed.netloc:
            return JSEARCH_OPENWEBNINJA_BASE_URL, "openwebninja"
        return host_value, "custom_url"

    if "openwebninja.com" in host_value:
        return JSEARCH_OPENWEBNINJA_BASE_URL, "openwebninja"

    return JSEARCH_RAPIDAPI_BASE_URL, "rapidapi"


def _build_headers(api_key: str, api_host: str, mode: str) -> dict:
    if mode == "openwebninja":
        return {
            "x-api-key": api_key,
            "Content-Type": "application/json",
        }

    if mode == "custom_url":
        return {"Authorization": f"Bearer {api_key}"}

    host_header = api_host
    if host_header.startswith("http://") or host_header.startswith("https://"):
        host_header = urlparse(host_header).netloc

    return {
        "X-RapidAPI-Key": api_key,
        "X-RapidAPI-Host": host_header or "jsearch.p.rapidapi.com",
    }


def _candidate_requests(api_key: str, api_host: str) -> list[tuple[str, dict, str]]:
    endpoint, mode = _resolve_endpoint_and_mode(api_host)
    attempts: list[tuple[str, dict, str]] = []
    seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()

    def add(endpoint_value: str, headers_value: dict, label: str) -> None:
        key = (
            endpoint_value,
            tuple(sorted((str(k), str(v)) for k, v in headers_value.items())),
        )
        if key in seen:
            return
        seen.add(key)
        attempts.append((endpoint_value, headers_value, label))

    # Preferred by config.
    add(endpoint, _build_headers(api_key=api_key, api_host=api_host, mode=mode), f"{mode}-default")

    # OpenWebNinja variants.
    add(
        JSEARCH_OPENWEBNINJA_BASE_URL,
        {"x-api-key": api_key, "Content-Type": "application/json"},
        "openwebninja-x-api-key",
    )
    add(
        JSEARCH_OPENWEBNINJA_BASE_URL,
        {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        "openwebninja-bearer",
    )

    # RapidAPI canonical fallback.
    add(
        JSEARCH_RAPIDAPI_BASE_URL,
        {"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"},
        "rapidapi-canonical",
    )

    return attempts


def _normalize_listings(results: list[dict]) -> list[JobListing]:
    listings = []
    for job in results:
        company = job.get("employer_name") or job.get("company_name") or "Unknown Company"
        location_parts = [job.get("job_city"), job.get("job_state"), job.get("job_country")]
        location = ", ".join(part for part in location_parts if part) or "Remote/Unspecified"

        description = (
            job.get("job_description")
            or _first_highlight(job, "Qualifications")
            or _first_highlight(job, "Responsibilities")
            or ""
        )

        listings.append(
            JobListing(
                title=job.get("job_title", "Unknown Title"),
                company=company,
                location=location,
                description=_truncate(description, max_chars=500),
                redirect_url=job.get("job_apply_link"),
                source="JSearch",
            )
        )

    return listings
