"""
youtube.py — YouTube study resource enrichment.

Responsibilities:
- Search YouTube for study resources relevant to roadmap phases
- Append clickable YouTube links to roadmap resources
- Gracefully no-op when the API key is not configured or requests fail
"""

import logging
from urllib.parse import quote_plus

import httpx

from config import get_settings
from models import CareerPlan

logger = logging.getLogger(__name__)
settings = get_settings()

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"


async def enrich_plan_with_youtube_links(
    plan: CareerPlan,
    target_role: str,
    missing_skills: list[str],
) -> CareerPlan:
    """
    Enrich each roadmap phase with YouTube study links.
    """
    if not settings.YOUTUBE_API_KEY:
        logger.info("YouTube API key not configured. Falling back to YouTube search links.")

    top_missing = missing_skills[:6]
    used_urls: set[str] = set()

    for index, phase in enumerate(plan.roadmap):
        queries = _build_phase_queries(
            phase_name=phase.phase,
            target_role=target_role,
            phase_goals=phase.goals,
            missing_skills=top_missing,
            phase_index=index,
        )

        links: list[str] = []
        for query in queries:
            links = await _fetch_youtube_playlists(
                query,
                settings.YOUTUBE_MAX_RESULTS_PER_PHASE,
                used_urls=used_urls,
            )
            if links:
                break

        if not links:
            links = [_build_youtube_search_link(queries[0])]
        if links:
            phase.resources.extend(links)
            for link in links:
                url = _extract_url(link)
                if url:
                    used_urls.add(url)

    return plan


def _build_phase_queries(
    phase_name: str,
    target_role: str,
    phase_goals: list[str],
    missing_skills: list[str],
    phase_index: int = 0,
) -> list[str]:
    phase_name_lower = phase_name.lower()
    goal_focus = [_compress_goal(goal) for goal in phase_goals if goal.strip()]
    missing_focus = [skill for skill in missing_skills if skill.strip()]

    if "foundation" in phase_name_lower:
        focus_terms = (
            missing_focus[:2]
            or goal_focus[:2]
            or [f"{target_role} basics"]
        )
    elif "skill building" in phase_name_lower or "phase 2" in phase_name_lower:
        focus_terms = (
            missing_focus[2:4]
            or goal_focus[:2]
            or [f"{target_role} intermediate"]
        )
    elif "project" in phase_name_lower or "job" in phase_name_lower or "phase 3" in phase_name_lower:
        focus_terms = goal_focus[:2] or [f"{target_role} projects portfolio interview prep"]
    else:
        focus_terms = goal_focus[:2] or missing_focus[:2] or [target_role]

    if phase_index == 0 and not focus_terms:
        focus_terms = [f"{target_role} basics"]

    queries = []
    for term in focus_terms:
        clean_term = term.strip()
        if clean_term:
            queries.append(f"{target_role} {clean_term} playlist")
            queries.append(f"{clean_term} full course playlist")

    queries.append(f"{target_role} roadmap playlist")
    return _unique_preserve_order(queries)


async def _fetch_youtube_playlists(query: str, max_results: int, used_urls: set[str]) -> list[str]:
    if not settings.YOUTUBE_API_KEY:
        return []

    params = {
        "part": "snippet",
        "q": query,
        "type": "playlist",
        "maxResults": max_results,
        "key": settings.YOUTUBE_API_KEY,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(YOUTUBE_SEARCH_URL, params=params)
            response.raise_for_status()
            data = response.json()

        results = []
        for item in data.get("items", []):
            playlist_id = item.get("id", {}).get("playlistId")
            title = item.get("snippet", {}).get("title", "Study playlist")
            channel = item.get("snippet", {}).get("channelTitle", "YouTube")
            if not playlist_id:
                continue
            url = f"https://www.youtube.com/playlist?list={quote_plus(playlist_id)}"
            if url in used_urls:
                continue
            results.append(f"Playlist: {title} by {channel} - {url}")
        return results
    except Exception as exc:
        logger.warning(f"Could not fetch YouTube playlists for query '{query}': {exc}")
        return []


def _build_youtube_search_link(query: str) -> str:
    return f"YouTube search: {query} - https://www.youtube.com/results?search_query={quote_plus(query)}"


def _compress_goal(goal: str) -> str:
    tokens = [
        token for token in goal.lower().split()
        if token.isalnum() and token not in {"learn", "build", "improve", "understand", "practice", "using", "with"}
    ]
    return " ".join(tokens[:5]) if tokens else goal


def _extract_url(text: str) -> str | None:
    parts = text.split(" - ")
    if parts and parts[-1].startswith("http"):
        return parts[-1]
    return None


def _unique_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered
