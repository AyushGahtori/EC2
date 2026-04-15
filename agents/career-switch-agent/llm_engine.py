"""
llm_engine.py — Google Gemini LLM Reasoning Engine.

Responsibilities:
- Build structured prompt from all data inputs
- Call Google Gemini via HTTP
- Parse LLM response into structured CareerPlan via JSON mode
- Validate with Pydantic; fallback gracefully on parse failure
"""

import json
import logging
import re
from collections import Counter
from typing import List

import httpx

from config import get_settings
from models import (
    CareerPlan,
    JobListing,
    OnetRole,
    SkillGapResult,
)

logger = logging.getLogger(__name__)
settings = get_settings()


# ─────────────────────────────────────────────────────────────
# SYSTEM PROMPT — defines agent identity + output contract
# ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are an AI Career Transition Agent integrated into a backend system.

SYSTEM CONTEXT:
- Backend: FastAPI (Python)
- Database: Firestore with cleaned O*NET role data
- Job Data: JSearch API, Adzuna API, and LinkedIn job listings
- Model Runtime: Google Gemini

You do NOT fetch data.
You ONLY analyze and generate intelligent outputs using the provided structured inputs.

INPUT PROVIDED TO YOU:
1. User profile
2. Precomputed skill gap
3. Target role data from Firestore
4. Job market data from JSearch, Adzuna, and LinkedIn
5. Interpreted hints from the backend, including skill-family equivalence and job-market signals

CRITICAL INSTRUCTIONS:
1. SKILL INTERPRETATION
- Treat related skills as equivalent when the backend hints show they belong to the same skill family.
- Do NOT assume the user lacks a skill if a closely related skill exists.

2. PRIORITIZATION
- For technical roles, prioritize technical skills, systems, tools, and implementation ability.
- De-emphasize generic soft skills like speaking or writing unless they are clearly important.

3. NO HALLUCINATION
- Use only the provided data.
- Do NOT assume industries, companies, tools, or requirements that do not appear in the inputs.
- Only mention company patterns when they are repeated or strongly supported by the job data.

4. JOB MARKET ANALYSIS
- Use the supplied job listings and job-market hints to identify common technologies, hiring trends, and role expectations.
- Ignore rare or irrelevant mentions.

5. PERSONALIZATION
- Do NOT recommend skills the user already has or skills covered by equivalent skill families.
- Build on existing skills and experience.
- Avoid beginner-level advice when the profile already shows relevant experience.

6. OUTPUT QUALITY
- Be specific, structured, practical, and job-readiness focused.
- No motivational fluff.

OUTPUT FORMAT:
Return ONLY a valid JSON object with exactly these keys:
{
  "career_summary": "<string>",
  "skill_gap_breakdown": {
    "core_skills": ["..."],
    "supporting_skills": ["..."],
    "optional_skills": ["..."]
  },
  "market_insights": {
    "top_companies_hiring": ["..."],
    "common_patterns": ["..."],
    "key_tools_and_technologies": ["..."],
    "demand_level": "low|moderate|high|very high"
  },
  "roadmap": [
    {
      "phase": "Phase 1: Foundations",
      "duration": "Weeks 1-4",
      "goals": ["..."],
      "resources": ["..."]
    },
    {
      "phase": "Phase 2: Skill Building",
      "duration": "Weeks 5-8",
      "goals": ["..."],
      "resources": ["..."]
    },
    {
      "phase": "Phase 3: Projects + Job Preparation",
      "duration": "Weeks 9-12",
      "goals": ["..."],
      "resources": ["..."]
    }
  ],
  "project_recommendations": [
    {
      "title": "...",
      "problem_solved": "...",
      "tech_stack": ["..."],
      "deliverable": "...",
      "resume_impact": "..."
    }
  ],
  "job_application_strategy": {
    "start_applying_at": "...",
    "target_role_types": ["..."],
    "tips": ["..."]
  },
  "final_advice": "<string>"
}
"""

_SOFT_SKILLS = {
    "active listening",
    "speaking",
    "writing",
    "reading comprehension",
    "critical thinking",
    "social perceptiveness",
    "coordination",
    "persuasion",
    "negotiation",
    "service orientation",
    "time management",
    "monitoring",
    "judgment and decision making",
    "complex problem solving",
    "active learning",
    "learning strategies",
    "instructing",
}

_TECH_ROLE_HINTS = {
    "developer", "engineer", "programmer", "data", "machine learning", "ml",
    "ai", "software", "security", "cloud", "devops", "analyst", "architect",
}

_TECH_KEYWORDS = {
    "python", "java", "javascript", "typescript", "c++", "c#", "go", "rust", "sql",
    "postgresql", "mysql", "mongodb", "nosql", "react", "node", "fastapi", "django",
    "flask", "api", "rest", "graphql", "docker", "kubernetes", "aws", "azure", "gcp",
    "git", "linux", "machine learning", "deep learning", "pytorch", "tensorflow",
    "scikit-learn", "pandas", "numpy", "spark", "airflow", "etl", "data pipeline",
    "statistics", "data analysis", "data visualization", "tableau", "power bi",
    "llm", "rag", "microservices", "testing", "automation", "system design",
    "algorithms", "data structures",
}

_SKILL_FAMILIES = {
    "programming": {"python", "java", "c++", "c", "c#", "javascript", "typescript", "go", "rust", "programming", "coding", "software development"},
    "problem solving": {"dsa", "data structures", "algorithms", "problem solving", "complex problem solving"},
    "data management": {"sql", "dbms", "database", "databases", "data management", "postgresql", "mysql", "mongodb", "nosql"},
    "machine learning": {"machine learning", "ml", "deep learning", "tensorflow", "pytorch", "scikit-learn"},
    "data analysis": {"data analysis", "analytics", "statistics", "pandas", "numpy", "excel", "power bi", "tableau", "business intelligence"},
    "api development": {"api", "apis", "rest", "rest api", "graphql", "fastapi", "flask", "django"},
    "frontend": {"react", "frontend", "html", "css", "javascript", "typescript", "web development"},
    "cloud and devops": {"aws", "azure", "gcp", "docker", "kubernetes", "devops", "ci/cd", "linux"},
}


def _normalize_skill(skill: str) -> str:
    return re.sub(r"\s+", " ", skill.strip().lower())


def _skill_family(skill: str) -> str | None:
    normalized = _normalize_skill(skill)
    for family, members in _SKILL_FAMILIES.items():
        if normalized in members:
            return family
    return None


def _skill_families_for(skills: List[str]) -> dict[str, List[str]]:
    families: dict[str, List[str]] = {}
    for skill in skills:
        family = _skill_family(skill)
        if family:
            families.setdefault(family, []).append(skill)
    return families


def _filter_missing_skills(user_skills: List[str], missing_skills: List[str]) -> List[str]:
    user_families = set(_skill_families_for(user_skills))
    filtered: List[str] = []
    for skill in missing_skills:
        family = _skill_family(skill)
        if family and family in user_families:
            continue
        filtered.append(skill)
    return filtered


def _is_technical_role(current_role: str, target_role: str, onet_role: OnetRole) -> bool:
    text = f"{current_role} {target_role} {onet_role.role} {onet_role.description}".lower()
    return any(hint in text for hint in _TECH_ROLE_HINTS)


def _job_text(job_listings: List[JobListing]) -> str:
    return " ".join(
        f"{job.title} {job.description} {job.company} {job.location}".lower()
        for job in job_listings
    )


def _extract_job_market_hints(job_listings: List[JobListing], onet_role: OnetRole) -> dict:
    if not job_listings:
        return {
            "repeated_companies": [],
            "common_technologies": [],
            "common_expectations": [],
        }

    company_counts = Counter(job.company.strip() for job in job_listings if job.company.strip())
    repeated_companies = [name for name, count in company_counts.items() if count > 1]

    job_blob = _job_text(job_listings)

    tech_candidates = set(onet_role.skills) | _TECH_KEYWORDS
    mentioned_tech = []
    for tech in tech_candidates:
        tech_norm = _normalize_skill(tech)
        if len(tech_norm) < 2:
            continue
        if tech_norm in job_blob:
            mentioned_tech.append(tech_norm)

    expectation_phrases = []
    for phrase in [
        "build APIs",
        "work with cross-functional teams",
        "deploy models",
        "write production code",
        "analyze data",
        "build dashboards",
        "design systems",
        "testing and debugging",
        "cloud deployment",
        "etl pipelines",
    ]:
        if phrase.lower() in job_blob:
            expectation_phrases.append(phrase)

    return {
        "repeated_companies": repeated_companies[:5],
        "common_technologies": sorted(set(mentioned_tech))[:15],
        "common_expectations": expectation_phrases[:8],
    }


def _prioritized_required_skills(onet_role: OnetRole, job_listings: List[JobListing], technical_role: bool) -> List[str]:
    job_blob = _job_text(job_listings)

    def score(skill: str) -> tuple[int, int, str]:
        normalized = _normalize_skill(skill)
        in_jobs = 1 if normalized in job_blob else 0
        is_soft = 1 if normalized in _SOFT_SKILLS else 0
        tech_bonus = 1 if normalized in _TECH_KEYWORDS else 0
        if technical_role:
            return (in_jobs + tech_bonus, -is_soft, normalized)
        return (in_jobs, -is_soft, normalized)

    ranked = sorted(onet_role.skills, key=score, reverse=True)
    return ranked[:20]


# ─────────────────────────────────────────────────────────────
# PROMPT BUILDER
# ─────────────────────────────────────────────────────────────
def _build_user_prompt(
    current_role: str,
    target_role: str,
    user_skills: List[str],
    experience_years: int,
    education: str,
    skill_gap: SkillGapResult,
    onet_role: OnetRole,
    job_listings: List[JobListing],
) -> str:
    technical_role = _is_technical_role(current_role, target_role, onet_role)
    filtered_missing_skills = _filter_missing_skills(user_skills, skill_gap.missing_skills)
    user_skill_families = _skill_families_for(user_skills)
    missing_skill_families = _skill_families_for(filtered_missing_skills)
    market_hints = _extract_job_market_hints(job_listings, onet_role)

    job_block = [
        {
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "description": job.description,
        }
        for job in job_listings
    ]

    payload = {
        "system_context": {
            "backend": "FastAPI",
            "database": "Firestore with cleaned O*NET data",
            "job_data": "JSearch API, Adzuna API, and LinkedIn",
            "model_runtime": "Google Gemini",
        },
        "user_profile": {
            "current_role": current_role,
            "target_role": target_role,
            "skills": user_skills,
            "experience_years": experience_years,
            "education": education,
        },
        "skill_gap": {
            "missing_skills": filtered_missing_skills,
            "matched_skills": skill_gap.matched_skills,
            "coverage_percent": skill_gap.coverage_percent,
        },
        "target_role_data": {
            "role": onet_role.role,
            "required_skills": skill_gap.required_skills,
            "tasks": onet_role.tasks[:10],
            "description": onet_role.description[:700],
        },
        "job_market_data": {
            "jobs": job_block,
        },
        "backend_interpretation_hints": {
            "technical_role": technical_role,
            "user_skill_families": user_skill_families,
            "filtered_missing_skill_families": missing_skill_families,
            "soft_skills_to_deemphasize": sorted(_SOFT_SKILLS),
            "job_market_signals": market_hints,
            "instruction": (
                "Treat skills in the same family as equivalent, avoid recommending already-covered families, "
                "and prioritize job-relevant technical skills over generic soft skills."
            ),
        },
    }

    return (
        "Analyze the following structured system input and return the required JSON only.\n\n"
        f"{json.dumps(payload, indent=2)}"
    )


# ─────────────────────────────────────────────────────────────
# LLM CALL
# ─────────────────────────────────────────────────────────────
async def generate_career_plan(
    current_role: str,
    target_role: str,
    user_skills: List[str],
    experience_years: int,
    education: str,
    skill_gap: SkillGapResult,
    onet_role: OnetRole,
    job_listings: List[JobListing],
) -> CareerPlan:
    """
    Calls Google Gemini with the structured prompt and validates the response
    into a CareerPlan Pydantic model.

    Raises:
        ValueError: If LLM response cannot be parsed into CareerPlan
    """
    user_prompt = _build_user_prompt(
        current_role=current_role,
        target_role=target_role,
        user_skills=user_skills,
        experience_years=experience_years,
        education=education,
        skill_gap=skill_gap,
        onet_role=onet_role,
        job_listings=job_listings,
    )

    logger.info(f"🤖 Calling Gemini ({settings.GEMINI_MODEL})...")

    if not settings.GEMINI_API_KEY:
        logger.error("❌ GEMINI_API_KEY is not configured.")
        raise ValueError("LLM call failed: GEMINI_API_KEY is missing.")

    payload = {
        "temperature": 0.2,
        "candidateCount": 1,
        "maxOutputTokens": 4096,
        "contents": [
            {
                "role": "user",
                "parts": [{"text": f"{SYSTEM_PROMPT}\n\n{user_prompt}"}],
            }
        ],
    }

    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.GEMINI_MODEL}:generateContent"
    )

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{endpoint}?key={settings.GEMINI_API_KEY}",
                headers={"Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        logger.error(f"❌ Gemini call failed: {e}")
        raise ValueError(f"LLM call failed: {e}") from e

    candidates = data.get("candidates") or []
    if not candidates:
        logger.error("❌ Gemini returned no candidates.")
        raise ValueError("LLM returned an empty response from Gemini.")

    parts = (((candidates[0] or {}).get("content") or {}).get("parts")) or []
    raw_content = "\n".join([str(part.get("text") or "").strip() for part in parts if isinstance(part, dict)])

    if not raw_content or not raw_content.strip():
        logger.error("❌ Gemini returned an empty response body.")
        raise ValueError("LLM returned an empty response from Gemini.")

    logger.debug(f"LLM raw response (first 500 chars): {raw_content[:500]}")

    # ── Parse JSON ──────────────────────────────────────────
    parsed_json = _extract_json(raw_content)

    # ── Validate with Pydantic ──────────────────────────────
    try:
        plan = CareerPlan(**parsed_json)
        logger.info("✅ CareerPlan validated successfully.")
        return plan
    except Exception as e:
        logger.error(f"❌ Pydantic validation failed: {e}")
        logger.error(f"Raw JSON: {json.dumps(parsed_json, indent=2)[:1000]}")
        raise ValueError(f"LLM output failed Pydantic validation: {e}") from e


# ─────────────────────────────────────────────────────────────
# JSON EXTRACTION HELPER
# ─────────────────────────────────────────────────────────────
def _extract_json(raw: str) -> dict:
    """
    Robustly extracts JSON from LLM output.
    Handles cases where the model wraps JSON in markdown fences.
    """
    # Strip markdown fences if present
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip()
    cleaned = cleaned.rstrip("```").strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to find first { ... } block
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

    raise ValueError(f"Could not extract valid JSON from LLM response. Raw: {raw[:500]}")
