"""
routes.py — FastAPI router for the Career Transition Agent.

Exposes:
  POST /api/career-plan   → main pipeline endpoint
  GET  /api/health        → service health check
  GET  /api/roles/search  → search O*NET roles (debug/discovery)
"""

import logging
import asyncio
import re
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException, Query

from adzuna import fetch_job_listings as fetch_adzuna_job_listings
from database import get_roles_collection, ping_database
from jsearch import fetch_job_listings as fetch_jsearch_job_listings
from linkedin import fetch_job_listings as fetch_linkedin_job_listings
from llm_engine import generate_career_plan
from models import (
    CareerRequest,
    CareerTransitionResponse,
    ErrorResponse,
    JobListing,
    OnetRole,
)
from skill_gap import compute_skill_gap, find_onet_role
from youtube import enrich_plan_with_youtube_links

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────────────────────────
# POST /api/career-plan — Main Pipeline
# ─────────────────────────────────────────────
@router.post(
    "/career-plan",
    response_model=CareerTransitionResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Role not found in O*NET"},
        422: {"description": "Validation error on request body"},
        503: {"model": ErrorResponse, "description": "LLM or DB unavailable"},
    },
    summary="Generate AI Career Transition Plan",
    description=(
        "Accepts user career profile, computes skill gaps using Firestore O*NET data, "
        "fetches live job market data from JSearch, Adzuna, and LinkedIn, and invokes Google Gemini to produce "
        "a structured, validated career transition plan."
    ),
)
async def generate_plan(
    request: CareerRequest,
    collection: Any = Depends(get_roles_collection),
):
    logger.info(
        f"📥 New request: {request.current_role} → {request.target_role} "
        f"| skills={len(request.skills)} | exp={request.experience_years}yrs"
    )

    # ── Step 1: Find current role in O*NET (context for LLM) ──
    current_onet = find_onet_role(request.current_role, collection)
    # Note: No 404 for current role — it's optional context

    # ── Step 2: Find TARGET role in O*NET (required) ──────────
    target_onet = find_onet_role(request.target_role, collection)
    if target_onet is None:
        logger.warning(f"Target role not found: '{request.target_role}'")
        raise HTTPException(
            status_code=404,
            detail=f"Target role '{request.target_role}' not found in the O*NET database. "
                   f"Try a broader title (e.g., 'Data Scientist' instead of 'Senior NLP Researcher').",
        )

    # ── Step 3: Fetch job market data from JSearch + Adzuna + LinkedIn ───
    jsearch_jobs, adzuna_jobs, linkedin_jobs = await asyncio.gather(
        fetch_jsearch_job_listings(request.target_role),
        fetch_adzuna_job_listings(request.target_role),
        fetch_linkedin_job_listings(request.target_role),
    )
    job_listings = _merge_job_listings(jsearch_jobs, adzuna_jobs, linkedin_jobs)

    # ── Step 4: Compute skill gap using O*NET + job signals ──
    skill_gap = compute_skill_gap(
        user_skills=request.skills,
        onet_role=target_onet,
        job_listings=job_listings,
    )

    # ── Step 5: Generate career plan via LLM ──────────────────
    try:
        career_plan = await generate_career_plan(
            current_role=request.current_role,
            target_role=request.target_role,
            user_skills=request.skills,
            experience_years=request.experience_years,
            education=request.education,
            skill_gap=skill_gap,
            onet_role=target_onet,
            job_listings=job_listings,
        )
        career_plan = await enrich_plan_with_youtube_links(
            plan=career_plan,
            target_role=request.target_role,
            missing_skills=skill_gap.missing_skills,
        )
    except ValueError as e:
        logger.error(f"LLM/parsing failure: {e}")
        raise HTTPException(
            status_code=503,
            detail=str(e),
        )

    # ── Step 6: Build and return response ─────────────────────
    return CareerTransitionResponse(
        success=True,
        request_summary={
            "current_role": request.current_role,
            "target_role": request.target_role,
            "matched_onet_role": target_onet.role,
            "experience_years": request.experience_years,
            "education": request.education,
            "skill_coverage_percent": skill_gap.coverage_percent,
        },
        skill_gap=skill_gap,
        job_market_count=len(job_listings),
        job_recommendations=job_listings[:12],
        career_plan=career_plan,
    )


def _merge_job_listings(*job_sources: list[JobListing]) -> list[JobListing]:
    merged: list[JobListing] = []
    seen: set[tuple[str, str, str]] = set()

    for source in job_sources:
        for job in source:
            key = (
                job.title.strip().lower(),
                job.company.strip().lower(),
                job.location.strip().lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(job)

    return merged


# ─────────────────────────────────────────────
# GET /api/health — Health Check
# ─────────────────────────────────────────────
@router.get(
    "/health",
    summary="System Health Check",
    description="Checks Firestore connectivity and returns service status.",
)
async def health_check():
    firestore_ok = ping_database()
    return {
        "status": "ok" if firestore_ok else "degraded",
        "services": {
            "firestore": "connected" if firestore_ok else "unreachable",
            "api": "running",
        },
    }


# ─────────────────────────────────────────────
# GET /api/roles/search — Role Discovery
# ─────────────────────────────────────────────
@router.get(
    "/roles/search",
    summary="Search O*NET Roles",
    description="Search for matching roles in the O*NET database. Useful for autocomplete.",
)
async def search_roles(
    q: str = Query(..., min_length=2, description="Role name to search"),
    limit: int = Query(10, ge=1, le=50, description="Max results"),
    collection: Any = Depends(get_roles_collection),
) -> List[dict]:
    """
    Returns a list of matched O*NET roles with their codes.
    Uses Firestore scan and simple relevance ranking.
    """
    try:
        query_text = q.strip().lower()
        docs = [doc.to_dict() for doc in collection.get()]

        if not query_text:
            return []

        candidates = []
        for doc in docs:
            role = str(doc.get("role", "")).lower()
            description = str(doc.get("description", "")).lower()
            if query_text in role or query_text in description:
                candidates.append(doc)

        if not candidates:
            keywords = [word for word in re.findall(r"\w+", query_text) if len(word) > 2]
            if keywords:
                pattern = re.compile("|".join(re.escape(word) for word in keywords), re.IGNORECASE)
                candidates = [
                    doc for doc in docs
                    if pattern.search(str(doc.get("role", "")) + " " + str(doc.get("description", "")))
                ]

        candidates.sort(
            key=lambda doc: (
                query_text in str(doc.get("role", "")).lower(),
                query_text in str(doc.get("description", "")).lower(),
                -len(str(doc.get("role", "")))
            ),
            reverse=True,
        )

        return [
            {
                "code": doc.get("code", ""),
                "role": doc.get("role", ""),
                "description": str(doc.get("description", ""))[:150],
            }
            for doc in candidates[:limit]
        ]
    except Exception as e:
        logger.error(f"Role search error: {e}")
        raise HTTPException(status_code=500, detail="Role search failed.")
