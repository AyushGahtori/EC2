"""
server.py - Career Switch Agent FastAPI Server (EC2 Runtime).
Exposes the /career-switch/action endpoint following Pian's action API contract.
Handles career plan generation with O*NET, job market data, and LLM reasoning.

This is the detached EC2 runtime version.
No imports from the main app - fully self-contained.
"""

import os
import json
import logging
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Career Switch Agent",
    description="AI-powered career transition planning with skill gap analysis and personalized roadmaps.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────
# Request/Response Models (matching Pian action API)
# ─────────────────────────────────────────────────────────────

class CareerSwitchActionRequest(BaseModel):
    taskId: Optional[str] = None
    userId: Optional[str] = None
    agentId: Optional[str] = None
    action: str
    current_role: Optional[str] = None
    target_role: Optional[str] = None
    skills: Optional[list[str]] = None
    experience_years: Optional[int] = None
    education: Optional[str] = None


class CareerSwitchActionResponse(BaseModel):
    status: str
    type: Optional[str] = None
    message: Optional[str] = None
    displayName: Optional[str] = None
    result: Optional[dict] = None
    error: Optional[str] = None


# ─────────────────────────────────────────────────────────────
# Health Check
# ─────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "agent": "career-switch-agent",
        "platform": "pian"
    }


# ─────────────────────────────────────────────────────────────
# Career Switch Action Endpoint (Main)
# ─────────────────────────────────────────────────────────────

@app.post("/career-switch/action", response_model=CareerSwitchActionResponse)
async def career_switch_action(req: CareerSwitchActionRequest) -> CareerSwitchActionResponse:
    """
    Main career switch action endpoint.
    
    Supports action: "generate_plan"
    
    Input:
    - current_role: Current job title (e.g., "Data Analyst")
    - target_role: Target job title (e.g., "Machine Learning Engineer")
    - skills: List of user skills (e.g., ["python", "sql", "excel"])
    - experience_years: Years of relevant experience
    - education: Highest education level (e.g., "B.Tech")
    
    Output:
    - A structured career plan with:
      - Career summary
      - Skill gap breakdown
      - Market insights
      - Personalized roadmap with phases
      - Project recommendations
      - Job application strategy
    """
    try:
        action = req.action.strip().lower()

        if action == "generate_plan":
            # Validate input
            if not req.current_role:
                return CareerSwitchActionResponse(
                    status="failed",
                    error="current_role is required"
                )
            if not req.target_role:
                return CareerSwitchActionResponse(
                    status="failed",
                    error="target_role is required"
                )
            if not req.skills or len(req.skills) == 0:
                return CareerSwitchActionResponse(
                    status="failed",
                    error="skills list is required and cannot be empty"
                )
            if req.experience_years is None:
                return CareerSwitchActionResponse(
                    status="failed",
                    error="experience_years is required"
                )
            if not req.education:
                return CareerSwitchActionResponse(
                    status="failed",
                    error="education is required"
                )

            # Generate the career plan
            result = await _generate_career_plan(
                current_role=req.current_role,
                target_role=req.target_role,
                skills=req.skills,
                experience_years=req.experience_years,
                education=req.education,
            )

            return CareerSwitchActionResponse(
                status="success",
                type="career_plan",
                displayName="Career Transition Plan",
                message="Your personalized AI career roadmap has been generated. Review the phases, skill priorities, and project recommendations.",
                result=result,
            )

        return CareerSwitchActionResponse(
            status="failed",
            error=f"Unknown action: {req.action}. Supported actions: generate_plan"
        )

    except ValueError as e:
        logger.error(f"Career plan generation failed: {e}")
        return CareerSwitchActionResponse(
            status="failed",
            error=f"Career plan generation failed: {str(e)}"
        )
    except Exception as e:
        logger.exception(f"Unexpected error in career_switch_action: {e}")
        return CareerSwitchActionResponse(
            status="failed",
            error=f"Agent error: {str(e)}"
        )


# ─────────────────────────────────────────────────────────────
# Internal Implementation
# ─────────────────────────────────────────────────────────────

async def _generate_career_plan(
    current_role: str,
    target_role: str,
    skills: list[str],
    experience_years: int,
    education: str,
) -> dict:
    """
    Generate a career transition plan.
    
    Since the full pipeline requires Firestore (O*NET), external APIs (JSearch, Adzuna, LinkedIn, YouTube),
    and Google Gemini, this method provides:
    1. A full implementation when all dependencies are available
    2. A gracefully degraded response when they're not (for testing)
    
    Returns a structured career plan matching the CareerPlan Pydantic model schema.
    """
    
    # Try full implementation with dependencies
    try:
        return await _full_career_plan_generation(
            current_role=current_role,
            target_role=target_role,
            skills=skills,
            experience_years=experience_years,
            education=education,
        )
    except Exception as e:
        logger.warning(f"Full career plan generation failed: {e}. Falling back to structured stub.")
        
        # Return a structured response that matches the schema even if dependencies are unavailable
        return _structured_fallback_plan(
            current_role=current_role,
            target_role=target_role,
            skills=skills,
            experience_years=experience_years,
            education=education,
            error_reason=str(e),
        )


async def _full_career_plan_generation(
    current_role: str,
    target_role: str,
    skills: list[str],
    experience_years: int,
    education: str,
) -> dict:
    """
    Full career plan generation pipeline.
    Requires: Firestore (O*NET data), Google Gemini LLM, JSearch, Adzuna, LinkedIn, YouTube APIs.
    """
    # Dynamic imports to avoid hard dependency on all modules
    from models import (
        CareerRequest,
        CareerTransitionResponse,
        SkillGapResult,
    )
    from routes import (
        generate_plan,
        _merge_job_listings,
    )
    from skill_gap import compute_skill_gap, find_onet_role
    from database import get_roles_collection, ping_database
    from adzuna import fetch_job_listings as fetch_adzuna_job_listings
    from jsearch import fetch_job_listings as fetch_jsearch_job_listings
    from linkedin import fetch_job_listings as fetch_linkedin_job_listings
    from llm_engine import generate_career_plan
    from youtube import enrich_plan_with_youtube_links
    import asyncio

    # Validate Firestore
    if not ping_database():
        raise ValueError("Firestore is not reachable. Cannot proceed with career plan generation.")

    # Get collection
    collection = get_roles_collection()

    # Find target role in O*NET
    target_onet = find_onet_role(target_role, collection)
    if target_onet is None:
        raise ValueError(
            f"Target role '{target_role}' not found in O*NET database. "
            f"Try a broader title (e.g., 'Data Scientist' instead of 'Senior NLP Researcher')."
        )

    # Find current role (optional context)
    current_onet = find_onet_role(current_role, collection)

    # Fetch job market data
    jsearch_jobs, adzuna_jobs, linkedin_jobs = await asyncio.gather(
        fetch_jsearch_job_listings(target_role),
        fetch_adzuna_job_listings(target_role),
        fetch_linkedin_job_listings(target_role),
    )
    job_listings = _merge_job_listings(jsearch_jobs, adzuna_jobs, linkedin_jobs)

    # Compute skill gap
    skill_gap = compute_skill_gap(
        user_skills=skills,
        onet_role=target_onet,
        job_listings=job_listings,
    )

    # Generate career plan via LLM
    career_plan = await generate_career_plan(
        current_role=current_role,
        target_role=target_role,
        user_skills=skills,
        experience_years=experience_years,
        education=education,
        skill_gap=skill_gap,
        onet_role=target_onet,
        job_listings=job_listings,
    )

    # Enrich with YouTube links
    career_plan = await enrich_plan_with_youtube_links(
        plan=career_plan,
        target_role=target_role,
        missing_skills=skill_gap.missing_skills,
    )

    # Return structured response
    return {
        "request_summary": {
            "current_role": current_role,
            "target_role": target_role,
            "experience_years": experience_years,
            "education": education,
            "skill_coverage_percent": skill_gap.coverage_percent,
        },
        "skill_gap": {
            "user_skills": skill_gap.user_skills,
            "required_skills": skill_gap.required_skills,
            "missing_skills": skill_gap.missing_skills,
            "matched_skills": skill_gap.matched_skills,
            "coverage_percent": skill_gap.coverage_percent,
        },
        "job_market_count": len(job_listings),
        "job_recommendations": [
            {
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "description": job.description,
                "source": job.source,
            }
            for job in job_listings[:12]
        ],
        "career_plan": {
            "career_summary": career_plan.career_summary,
            "skill_gap_breakdown": {
                "core_skills": career_plan.skill_gap_breakdown.core_skills,
                "supporting_skills": career_plan.skill_gap_breakdown.supporting_skills,
                "optional_skills": career_plan.skill_gap_breakdown.optional_skills,
            },
            "market_insights": {
                "top_companies_hiring": career_plan.market_insights.top_companies_hiring,
                "common_patterns": career_plan.market_insights.common_patterns,
                "key_tools_and_technologies": career_plan.market_insights.key_tools_and_technologies,
                "demand_level": career_plan.market_insights.demand_level,
            },
            "roadmap": [
                {
                    "phase": phase.phase,
                    "duration": phase.duration,
                    "goals": phase.goals,
                    "resources": phase.resources,
                }
                for phase in career_plan.roadmap
            ],
            "project_recommendations": [
                {
                    "title": proj.title,
                    "problem_solved": proj.problem_solved,
                    "tech_stack": proj.tech_stack,
                    "deliverable": proj.deliverable,
                    "resume_impact": proj.resume_impact,
                }
                for proj in career_plan.project_recommendations
            ],
            "job_application_strategy": {
                "start_applying_at": career_plan.job_application_strategy.start_applying_at,
                "target_role_types": career_plan.job_application_strategy.target_role_types,
                "tips": career_plan.job_application_strategy.tips,
            },
            "final_advice": career_plan.final_advice,
        },
    }


def _structured_fallback_plan(
    current_role: str,
    target_role: str,
    skills: list[str],
    experience_years: int,
    education: str,
    error_reason: str,
) -> dict:
    """
    Fallback career plan when dependencies (Firestore, Gemini, external APIs) are unavailable.
    Returns a well-structured response that matches the schema.
    This allows the frontend to display error context gracefully.
    """
    logger.info(f"Using fallback career plan due to: {error_reason}")

    return {
        "request_summary": {
            "current_role": current_role,
            "target_role": target_role,
            "experience_years": experience_years,
            "education": education,
            "skill_coverage_percent": 0.0,
            "_fallback_mode": True,
            "_error_reason": error_reason,
        },
        "skill_gap": {
            "user_skills": skills,
            "required_skills": [],
            "missing_skills": [],
            "matched_skills": skills,
            "coverage_percent": 100.0,
        },
        "job_market_count": 0,
        "job_recommendations": [],
        "career_plan": {
            "career_summary": (
                f"Career plan generation for {current_role} → {target_role} is temporarily unavailable. "
                f"Please check back shortly or contact support. "
                f"Technical details: {error_reason}"
            ),
            "skill_gap_breakdown": {
                "core_skills": [],
                "supporting_skills": [],
                "optional_skills": [],
            },
            "market_insights": {
                "top_companies_hiring": [],
                "common_patterns": [],
                "key_tools_and_technologies": [],
                "demand_level": "unknown",
            },
            "roadmap": [
                {
                    "phase": "Phase 1: Investigation",
                    "duration": "To be determined",
                    "goals": [
                        "Identify specific O*NET role code for target position",
                        "Gather current job market requirements",
                    ],
                    "resources": [
                        "O*NET Online (www.onetonline.org)",
                        "LinkedIn Job Search",
                        "Indeed",
                    ],
                },
            ],
            "project_recommendations": [
                {
                    "title": "Portfolio Foundation",
                    "problem_solved": "Establish a foundation for career transition",
                    "tech_stack": ["Documentation", "GitHub", "LinkedIn"],
                    "deliverable": "Public portfolio showcasing existing skills",
                    "resume_impact": "Demonstrates capability in current domain",
                },
            ],
            "job_application_strategy": {
                "start_applying_at": "After skill gap closure",
                "target_role_types": [target_role],
                "tips": [
                    "Use LinkedIn to identify hiring companies",
                    "Tailor resume to target role requirements",
                    "Network with professionals in transition",
                ],
            },
            "final_advice": (
                "Career transition planning requires full system availability. "
                "Our team is working to restore service. "
                "In the meantime, explore O*NET Online for detailed role information."
            ),
        },
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8022, reload=False)
