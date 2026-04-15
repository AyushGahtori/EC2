"""
models.py — Pydantic schemas for request validation and structured LLM output.
Every section of the career plan is strictly typed.
"""

from pydantic import BaseModel, Field, field_validator
from typing import List, Optional


# ─────────────────────────────────────────────
# 1. REQUEST MODEL
# ─────────────────────────────────────────────

class CareerRequest(BaseModel):
    current_role: str = Field(..., min_length=2, max_length=120, examples=["Data Analyst"])
    target_role: str = Field(..., min_length=2, max_length=120, examples=["Machine Learning Engineer"])
    skills: List[str] = Field(..., min_length=1, examples=[["python", "sql", "excel"]])
    experience_years: int = Field(..., ge=0, le=50, examples=[2])
    education: str = Field(..., min_length=2, max_length=100, examples=["B.Tech"])

    @field_validator("skills", mode="before")
    @classmethod
    def normalize_skills(cls, v):
        """Lowercase + strip all user-provided skills."""
        return [s.strip().lower() for s in v if s.strip()]


# ─────────────────────────────────────────────
# 2. INTERMEDIATE DATA MODELS (internal use)
# ─────────────────────────────────────────────

class OnetRole(BaseModel):
    """Represents a matched O*NET role document from Firestore."""
    code: str
    role: str
    description: str
    skills: List[str]
    tasks: List[str]


class JobListing(BaseModel):
    """A single JSearch job listing."""
    title: str
    company: str
    location: str
    description: str
    redirect_url: Optional[str] = None
    source: str = "Unknown"


class SkillGapResult(BaseModel):
    """Output of the skill gap computation module."""
    user_skills: List[str]
    required_skills: List[str]
    missing_skills: List[str]
    matched_skills: List[str]
    coverage_percent: float = Field(..., ge=0.0, le=100.0)


# ─────────────────────────────────────────────
# 3. STRUCTURED LLM OUTPUT MODELS
# ─────────────────────────────────────────────

class SkillGapBreakdown(BaseModel):
    core_skills: List[str] = Field(default_factory=list)
    supporting_skills: List[str] = Field(default_factory=list)
    optional_skills: List[str] = Field(default_factory=list)


class MarketInsights(BaseModel):
    top_companies_hiring: List[str] = Field(default_factory=list)
    common_patterns: List[str] = Field(default_factory=list)
    key_tools_and_technologies: List[str] = Field(default_factory=list)
    demand_level: str = Field(default="moderate", examples=["low", "moderate", "high", "very high"])


class RoadmapPhase(BaseModel):
    phase: str
    duration: str
    goals: List[str]
    resources: List[str] = Field(default_factory=list)


class ProjectRecommendation(BaseModel):
    title: str
    problem_solved: str
    tech_stack: List[str]
    deliverable: str
    resume_impact: str


class JobApplicationStrategy(BaseModel):
    start_applying_at: str
    target_role_types: List[str]
    tips: List[str]


class CareerPlan(BaseModel):
    """
    The complete, strictly validated career transition plan.
    This is the top-level response model returned by the API.
    """
    career_summary: str
    skill_gap_breakdown: SkillGapBreakdown
    market_insights: MarketInsights
    roadmap: List[RoadmapPhase]
    project_recommendations: List[ProjectRecommendation]
    job_application_strategy: JobApplicationStrategy
    final_advice: str


# ─────────────────────────────────────────────
# 4. API RESPONSE WRAPPER
# ─────────────────────────────────────────────

class CareerTransitionResponse(BaseModel):
    """Top-level API response envelope."""
    success: bool = True
    request_summary: dict          # echoes back current/target role, coverage
    skill_gap: SkillGapResult
    job_market_count: int          # number of JSearch listings found
    job_recommendations: List[JobListing] = Field(default_factory=list)
    career_plan: CareerPlan


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    detail: Optional[str] = None
