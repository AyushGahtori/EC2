"""
Tool registry — all agent tools are imported and exported from here.
The agent binds these tools to the LLM dynamically.
"""
from app.tools.google_search import google_search
from app.tools.google_maps import google_maps
from app.tools.linkedin_search import linkedin_search
from app.tools.company_enrichment import company_enrichment
from app.tools.email_finder import email_finder
from app.tools.lead_scoring import lead_scoring
from app.tools.storage import storage

ALL_TOOLS = [
    google_search,
    google_maps,
    linkedin_search,
    company_enrichment,
    email_finder,
    lead_scoring,
    storage,
]

TOOL_MAP = {t.name: t for t in ALL_TOOLS}

__all__ = [
    "google_search",
    "google_maps",
    "linkedin_search",
    "company_enrichment",
    "email_finder",
    "lead_scoring",
    "storage",
    "ALL_TOOLS",
    "TOOL_MAP",
]
