"""
skill_gap.py — Skill Gap Computation Module.

Responsibilities:
- Search Firestore O*NET collection for best role match
- Compute missing skills, matched skills, and coverage %
- Return structured SkillGapResult + OnetRole

Matching strategy:
  1. Alias map (exact O*NET title lookup for common user-facing roles)
  2. Firestore document scan with Python-side ranking
  3. Regex fallback on individual keywords
  4. Python-side re-ranking: 70% word-overlap + 30% sequence similarity
"""

import logging
import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Any, Optional

from models import JobListing, OnetRole, SkillGapResult

logger = logging.getLogger(__name__)

SKILL_MAP: dict[str, str] = {
    "python": "programming",
    "java": "programming",
    "c++": "programming",
    "c": "programming",
    "c#": "programming",
    "javascript": "programming",
    "typescript": "programming",
    "go": "programming",
    "rust": "programming",
    "programming": "programming",
    "coding": "programming",
    "software development": "programming",
    "machine learning": "machine learning",
    "ml": "machine learning",
    "deep learning": "machine learning",
    "tensorflow": "machine learning",
    "pytorch": "machine learning",
    "scikit-learn": "machine learning",
    "sql": "databases",
    "dbms": "databases",
    "database": "databases",
    "databases": "databases",
    "data management": "databases",
    "postgresql": "databases",
    "mysql": "databases",
    "mongodb": "databases",
    "nosql": "databases",
    "dsa": "data structures and algorithms",
    "algorithms": "data structures and algorithms",
    "data structures": "data structures and algorithms",
    "problem solving": "data structures and algorithms",
    "complex problem solving": "data structures and algorithms",
    "api": "apis",
    "apis": "apis",
    "rest": "apis",
    "rest api": "apis",
    "graphql": "apis",
    "git": "version control",
    "github": "version control",
    "gitlab": "version control",
    "version control": "version control",
    "testing": "debugging and testing",
    "debugging": "debugging and testing",
    "troubleshooting": "debugging and testing",
    "quality control analysis": "debugging and testing",
    "systems evaluation": "debugging and testing",
    "technology design": "software design",
    "system design": "software design",
    "systems analysis": "software design",
    "data analysis": "data analysis",
    "analytics": "data analysis",
    "pandas": "data analysis",
    "numpy": "data analysis",
    "excel": "spreadsheets and bi tools",
    "power bi": "spreadsheets and bi tools",
    "tableau": "spreadsheets and bi tools",
    "data visualization": "spreadsheets and bi tools",
    "statistics": "statistics",
    "statistical analysis": "statistics",
    "probability": "statistics",
    "aws": "cloud platforms",
    "azure": "cloud platforms",
    "gcp": "cloud platforms",
    "cloud": "cloud platforms",
    "docker": "containers and ci/cd",
    "kubernetes": "containers and ci/cd",
    "ci/cd": "containers and ci/cd",
    "devops": "containers and ci/cd",
    "linux": "linux",
    "etl": "data engineering",
    "data pipeline": "data engineering",
    "data pipelines": "data engineering",
    "data engineering": "data engineering",
    "airflow": "data engineering",
    "model deployment": "model deployment",
    "deployment": "model deployment",
    "mlops": "model deployment",
    "communication": "communication",
}

IGNORE_SKILLS = {
    "speaking",
    "writing",
    "active listening",
    "social perceptiveness",
    "coordination",
    "time management",
    "reading comprehension",
    "active learning",
    "learning strategies",
    "monitoring",
    "instructing",
    "persuasion",
    "negotiation",
    "service orientation",
    "judgment and decision making",
    "critical thinking",
    "mathematics",
    "operations analysis",
}

TECH_ROLE_HINTS = {
    "developer",
    "engineer",
    "programmer",
    "software",
    "web",
    "machine learning",
    "data",
    "analyst",
    "scientist",
    "security",
    "cloud",
    "devops",
    "architect",
}

ROLE_SKILL_PROFILES: list[tuple[tuple[str, ...], list[str]]] = [
    (
        ("software developer", "software developers", "software engineer", "programmer"),
        [
            "programming",
            "data structures and algorithms",
            "software design",
            "debugging and testing",
            "apis",
            "databases",
            "version control",
        ],
    ),
    (
        ("web developer", "frontend", "full stack"),
        [
            "programming",
            "frontend development",
            "apis",
            "debugging and testing",
            "version control",
        ],
    ),
    (
        ("data analyst", "business intelligence"),
        [
            "databases",
            "data analysis",
            "spreadsheets and bi tools",
            "statistics",
        ],
    ),
    (
        ("data scientist",),
        [
            "programming",
            "machine learning",
            "statistics",
            "data analysis",
            "databases",
            "data engineering",
        ],
    ),
    (
        ("machine learning engineer", "ml engineer", "ai engineer"),
        [
            "programming",
            "machine learning",
            "statistics",
            "data engineering",
            "model deployment",
            "apis",
        ],
    ),
    (
        ("data engineer", "database administrator", "database architect"),
        [
            "databases",
            "data engineering",
            "programming",
            "cloud platforms",
        ],
    ),
    (
        ("security", "cybersecurity"),
        [
            "linux",
            "programming",
            "cloud platforms",
            "debugging and testing",
        ],
    ),
]

# Stop words excluded from word-overlap scoring
_STOP_WORDS = {"and", "or", "the", "of", "in", "for", "a", "an", "to", "with"}

# ─────────────────────────────────────────────────────────────
# ROLE ALIAS MAP
# Maps common user-facing job titles → exact O*NET role names
# All values verified against live Firestore O*NET collection (854 docs).
# ─────────────────────────────────────────────────────────────
_ROLE_ALIASES: dict[str, str] = {
    # ── Software Engineering ──────────────────────────────────
    "software engineer":            "Software Developers",
    "backend developer":            "Software Developers",
    "backend engineer":             "Software Developers",
    "full stack developer":         "Software Developers",
    "fullstack developer":          "Software Developers",
    "full stack engineer":          "Software Developers",
    "platform engineer":            "Software Developers",
    "frontend developer":           "Web Developers",
    "frontend engineer":            "Web Developers",
    "web developer":                "Web Developers",
    "web designer":                 "Web Developers",
    "devops engineer":              "Software Quality Assurance Analysts and Testers",
    "devops":                       "Software Quality Assurance Analysts and Testers",
    "site reliability engineer":    "Computer Network Architects",
    "sre":                          "Computer Network Architects",
    "cloud engineer":               "Network and Computer Systems Administrators",
    "network engineer":             "Network and Computer Systems Administrators",
    "systems administrator":        "Network and Computer Systems Administrators",
    "sysadmin":                     "Network and Computer Systems Administrators",
    "embedded engineer":            "Computer Hardware Engineers",
    "hardware engineer":            "Computer Hardware Engineers",
    # ── AI / ML / Research ───────────────────────────────────
    "machine learning engineer":    "Computer and Information Research Scientists",
    "ml engineer":                  "Computer and Information Research Scientists",
    "ai engineer":                  "Computer and Information Research Scientists",
    "ai researcher":                "Computer and Information Research Scientists",
    "research scientist":           "Computer and Information Research Scientists",
    "deep learning engineer":       "Computer and Information Research Scientists",
    "nlp engineer":                 "Computer and Information Research Scientists",
    # ── Data Science / Analytics ─────────────────────────────
    "data scientist":               "Operations Research Analysts",
    "senior data scientist":        "Operations Research Analysts",
    "applied scientist":            "Operations Research Analysts",
    "data analyst":                 "Business Intelligence Analysts",
    "analytics engineer":           "Business Intelligence Analysts",
    "bi developer":                 "Business Intelligence Analysts",
    "bi analyst":                   "Business Intelligence Analysts",
    "data engineer":                "Database Administrators",
    "database engineer":            "Database Administrators",
    "etl developer":                "Database Administrators",
    "data warehouse engineer":      "Data Warehousing Specialists",
    "quantitative analyst":         "Financial Quantitative Analysts",
    "quant analyst":                "Financial Quantitative Analysts",
    "business analyst":             "Management Analysts",
    # ── Security ─────────────────────────────────────────────
    "cybersecurity analyst":        "Information Security Analysts",
    "security analyst":             "Information Security Analysts",
    "penetration tester":           "Information Security Analysts",
    "ethical hacker":               "Information Security Analysts",
    "security engineer":            "Information Security Engineers",
    # ── Management ───────────────────────────────────────────
    "product manager":              "Computer and Information Systems Managers",
    "it manager":                   "Computer and Information Systems Managers",
    "engineering manager":          "Computer and Information Systems Managers",
    "cto":                          "Computer and Information Systems Managers",
    # ── Specialist Roles ─────────────────────────────────────
    "computer programmer":          "Computer Programmers",
    "programmer":                   "Computer Programmers",
    "systems analyst":              "Computer Systems Analysts",
    "it support":                   "Computer User Support Specialists",
    "help desk":                    "Computer User Support Specialists",
    "statistician":                 "Statisticians",
    "biostatistician":              "Biostatisticians",
    "gis analyst":                  "Geographic Information Systems Technologists and Technicians",
    "gis specialist":               "Geographic Information Systems Technologists and Technicians",
    "ux designer":                  "Web Developers",
    "ui designer":                  "Web Developers",
}


# ─────────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────────
def _words(s: str) -> set[str]:
    """Tokenize a role name into meaningful words (lowercased, stop-words removed)."""
    return {w for w in re.split(r"[\s/\-]+", s.lower()) if w and w not in _STOP_WORDS}


def _word_overlap(query: str, candidate: str) -> float:
    """Jaccard-like score: shared words / union of all words."""
    q, c = _words(query), _words(candidate)
    if not q and not c:
        return 0.0
    return len(q & c) / len(q | c)


def _sequence_sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _score(query: str, candidate: str) -> float:
    """
    Combined relevance score.
    - 70% word-overlap (so 'Data Scientist' beats 'Animal Scientist')
    - 30% sequence similarity (handles typos / alternate orderings)
    """
    return 0.70 * _word_overlap(query, candidate) + 0.30 * _sequence_sim(query, candidate)


def normalize_skills(skills: list[str]) -> list[str]:
    """
    Normalize skill variants into comparable buckets before matching.
    Unknown skills are preserved in lowercase form.
    """
    normalized: list[str] = []
    for skill in skills:
        normalized_skill = skill.lower().strip()
        normalized.append(SKILL_MAP.get(normalized_skill, normalized_skill))
    return normalized


def _contains_phrase(text: str, phrase: str) -> bool:
    pattern = r"(?<!\w)" + re.escape(phrase).replace(r"\ ", r"\s+") + r"(?!\w)"
    return re.search(pattern, text) is not None


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _is_technical_role(onet_role: OnetRole) -> bool:
    role_text = f"{onet_role.role} {onet_role.description}".lower()
    return any(hint in role_text for hint in TECH_ROLE_HINTS)


def _profile_skills_for_role(onet_role: OnetRole) -> list[str]:
    role_text = f"{onet_role.role} {onet_role.description}".lower()
    matched: list[str] = []
    for keywords, skills in ROLE_SKILL_PROFILES:
        if any(keyword in role_text for keyword in keywords):
            matched.extend(skills)
    return _dedupe_preserve_order(matched)


def _extract_skills_from_text(text: str) -> list[str]:
    lowered = re.sub(r"\s+", " ", text.lower())
    matched: list[str] = []
    for alias, canonical in SKILL_MAP.items():
        if canonical in IGNORE_SKILLS:
            continue
        if _contains_phrase(lowered, alias):
            matched.append(canonical)

    if "test" in lowered or "testing" in lowered or "validation" in lowered:
        matched.append("debugging and testing")
    if "database" in lowered or "data store" in lowered:
        matched.append("databases")
    if "interface" in lowered or "architecture" in lowered or "design" in lowered:
        matched.append("software design")
    if "deploy" in lowered or "production" in lowered:
        matched.append("model deployment")
    if "github" in lowered or "git" in lowered:
        matched.append("version control")

    return _dedupe_preserve_order(matched)


def _extract_market_skill_counts(job_listings: list[JobListing]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for job in job_listings:
        job_text = " ".join(
            [
                job.title or "",
                job.description or "",
                job.company or "",
            ]
        )
        for skill in _extract_skills_from_text(job_text):
            counts[skill] += 1
    return counts


def _derive_required_skills(onet_role: OnetRole, job_listings: list[JobListing]) -> list[str]:
    technical_role = _is_technical_role(onet_role)
    role_profile = _profile_skills_for_role(onet_role)

    onet_skills = [
        skill
        for skill in normalize_skills(onet_role.skills)
        if skill and skill not in IGNORE_SKILLS
    ]
    role_text_skills = _extract_skills_from_text(
        " ".join([onet_role.role, onet_role.description, *onet_role.tasks])
    )
    market_counts = _extract_market_skill_counts(job_listings)
    if job_listings:
        threshold = 2 if len(job_listings) >= 4 else 1
        market_skills = [
            skill
            for skill, count in market_counts.most_common()
            if count >= threshold
        ]
    else:
        market_skills = []

    combined: list[str] = []
    if technical_role:
        combined.extend(role_profile)
        combined.extend(market_skills)
        combined.extend(role_text_skills)
        combined.extend(onet_skills)
    else:
        combined.extend(onet_skills)
        combined.extend(role_text_skills)
        combined.extend(market_skills)

    required_skills = _dedupe_preserve_order(combined)

    if technical_role:
        required_skills = [
            skill for skill in required_skills
            if skill not in {"communication"}
        ]

    return required_skills[:12]


# ─────────────────────────────────────────────────────────────
# STEP 1: Find the best matching O*NET role in Firestore
# ─────────────────────────────────────────────────────────────
def find_onet_role(role_query: str, collection: Any) -> Optional[OnetRole]:
    """
    Multi-strategy role lookup:

    1. Alias map  — instant exact match for well-known user-facing titles.
    2. Firestore document scan — retrieve documents and rank in memory.
    3. Regex fallback — if the initial scan yields nothing, search by keywords.
    4. Python re-ranking — re-scores all candidates with _score() and picks best.

    Returns None if best score is below 0.25.
    """
    role_query_clean = role_query.strip()

    # ── Strategy 1: Alias map (case-insensitive exact match) ──────────────
    alias_key = role_query_clean.lower()
    if alias_key in _ROLE_ALIASES:
        mapped = _ROLE_ALIASES[alias_key]
        logger.info(f"Alias hit: '{role_query_clean}' → '{mapped}'")
        role_query_clean = mapped  # Use exact O*NET name for document search

    # ── Strategy 2: Firestore scan and Python ranking ─────────────────────
    try:
        docs = [doc.to_dict() for doc in collection.get()]
    except Exception as exc:
        logger.error(f"Failed to read role documents from Firestore: {exc}")
        return None

    candidates: list[dict] = []
    exact_matches = [
        doc for doc in docs
        if str(doc.get("role", "")).strip().lower() == role_query_clean.lower()
    ]

    if exact_matches:
        candidates = exact_matches[:20]
    else:
        logger.info(f"No exact Firestore role match for '{role_query_clean}'. Scanning documents...")
        candidates = docs[:20]

    # ── Strategy 3: Regex keyword fallback ────────────────────────────────
    if not candidates:
        logger.warning(f"No candidates at all for '{role_query_clean}', trying keyword regex...")
        keywords = [re.escape(w) for w in _words(role_query_clean) if len(w) > 2]
        if keywords:
            pattern = re.compile("|".join(keywords), re.IGNORECASE)
            candidates = [
                doc for doc in docs
                if pattern.search(str(doc.get("role", "")))
            ]

    if not candidates:
        logger.error(f"No O*NET candidates at all for: '{role_query_clean}'")
        return None

    # ── Strategy 4: Re-rank by combined score ─────────────────────────────
    scored = sorted(
        [(doc, _score(role_query_clean, doc.get("role", ""))) for doc in candidates],
        key=lambda x: x[1],
        reverse=True,
    )

    best_doc, best_score = scored[0]
    logger.info(
        f"Best match for '{role_query_clean}': "
        f"'{best_doc.get('role')}' (score={best_score:.3f})"
    )
    for doc, sc in scored[:3]:
        logger.debug(f"  → '{doc.get('role')}' score={sc:.3f}")

    if best_score < 0.25:
        logger.warning(f"Score {best_score:.3f} below threshold. Rejecting match.")
        return None

    return OnetRole(
        code=best_doc.get("code", ""),
        role=best_doc.get("role", ""),
        description=best_doc.get("description", ""),
        skills=[s.strip().lower() for s in (best_doc.get("skills") or [])],
        tasks=best_doc.get("tasks") or [],
    )


# ─────────────────────────────────────────────────────────────
# STEP 2: Compute skill gap
# ─────────────────────────────────────────────────────────────
def compute_skill_gap(
    user_skills: list[str],
    onet_role: OnetRole,
    job_listings: Optional[list[JobListing]] = None,
) -> SkillGapResult:
    """
    Set-based skill comparison.

    - user_skills: already normalized (lowercase, stripped) by Pydantic validator
    - onet_role.skills: normalized in find_onet_role()

    Returns SkillGapResult with missing/matched lists and coverage %.
    """
    normalized_user_skills = normalize_skills(user_skills)
    user_set = set(
        skill for skill in normalized_user_skills
        if skill and skill not in IGNORE_SKILLS
    )
    required_set = set(_derive_required_skills(onet_role, job_listings or []))

    matched = sorted(user_set & required_set)
    missing = sorted(required_set - user_set)
    coverage = (len(matched) / len(required_set) * 100) if required_set else 100.0

    logger.info(
        f"Skill gap | required={len(required_set)} "
        f"matched={len(matched)} missing={len(missing)} "
        f"coverage={coverage:.1f}%"
    )

    return SkillGapResult(
        user_skills=sorted(user_set),
        required_skills=sorted(required_set),
        missing_skills=missing,
        matched_skills=matched,
        coverage_percent=round(coverage, 2),
    )
