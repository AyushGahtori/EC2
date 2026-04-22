"""
adzuna.py — Adzuna Job Market Data Service.

Responsibilities:
- Call Adzuna Job Search API for the target role
- Parse and normalize response into JobListing objects
- Graceful fallback if API is unavailable or returns no results
"""

import logging
from typing import List

import httpx

from config import get_settings
from models import JobListing

logger = logging.getLogger(__name__)
settings = get_settings()

ADZUNA_BASE_URL = "https://api.adzuna.com/v1/api/jobs"


async def fetch_job_listings(target_role: str) -> List[JobListing]:
    """
    Fetch live job listings from Adzuna for a given role.

    Args:
        target_role: The job title to search for (e.g., "Machine Learning Engineer")

    Returns:
        List of JobListing objects (empty list on failure)
    """
    if not settings.ADZUNA_APP_ID or not settings.ADZUNA_APP_KEY:
        logger.warning("⚠️  Adzuna credentials not set. Returning empty job list.")
        return []

    url = (
        f"{ADZUNA_BASE_URL}/{settings.ADZUNA_COUNTRY}/search/1"
    )
    params = {
        "app_id": settings.ADZUNA_APP_ID,
        "app_key": settings.ADZUNA_APP_KEY,
        "what": target_role,
        "results_per_page": settings.ADZUNA_MAX_RESULTS,
        "content-type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

        results = data.get("results", [])
        logger.info(f"✅ Adzuna returned {len(results)} listings for '{target_role}'")

        listings = []
        for job in results:
            listings.append(
                JobListing(
                    title=job.get("title", "Unknown Title"),
                    company=job.get("company", {}).get("display_name", "Unknown Company"),
                    location=job.get("location", {}).get("display_name", "Remote/Unspecified"),
                    description=_truncate(job.get("description", ""), max_chars=400),
                    redirect_url=job.get("redirect_url"),
                    source="Adzuna",
                )
            )
        return listings

    except httpx.HTTPStatusError as e:
        logger.error(f"❌ Adzuna API HTTP error: {e.response.status_code} — {e.response.text[:200]}")
        return []
    except httpx.RequestError as e:
        logger.error(f"❌ Adzuna API request failed: {e}")
        return []
    except Exception as e:
        logger.error(f"❌ Unexpected error fetching Adzuna data: {e}")
        return []


def _truncate(text: str, max_chars: int) -> str:
    """Trim job description to avoid bloating the LLM prompt."""
    return text[:max_chars].strip() + ("..." if len(text) > max_chars else "")
