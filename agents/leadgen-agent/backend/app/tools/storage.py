"""
Storage tool — persists enriched leads to MongoDB.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from langchain_core.tools import tool

from app.services.mongodb_client import upsert_lead
from app.tools.lead_scoring import fallback_score_from_lead, lead_scoring

logger = logging.getLogger(__name__)


def _first_non_empty(*values: Any) -> str:
    for value in values:
        if isinstance(value, list):
            for item in value:
                if item:
                    return str(item).strip()
            continue
        if value:
            return str(value).strip()
    return ""


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _needs_scoring(lead: dict) -> bool:
    """Detect placeholder or missing scores that should be replaced."""
    score = _coerce_int(lead.get("score"), -1)
    reasoning = _first_non_empty(lead.get("score_reasoning"), lead.get("reasoning"))
    return score < 0 or score == 0 or (score == 50 and not reasoning)


async def _ensure_score(lead: dict, original_query: str = "") -> dict:
    """Score a lead before persistence if it was never meaningfully scored."""
    scored = dict(lead)
    if not _needs_scoring(scored):
        return scored

    if original_query:
        try:
            result = await lead_scoring.ainvoke({
                "lead_data": json.dumps(scored, ensure_ascii=False),
                "original_query": original_query,
            })
            data = json.loads(result) if isinstance(result, str) else result
            if "score" in data:
                scored["score"] = _coerce_int(data.get("score"), 0)
                scored["score_reasoning"] = _first_non_empty(
                    data.get("reasoning"),
                    scored.get("score_reasoning"),
                )
                return scored
        except Exception as exc:
            logger.warning(f"Auto-scoring before storage failed for {lead.get('name')}: {exc}")

    fallback = fallback_score_from_lead(
        scored,
        reason="Scored during storage using fallback completeness heuristics",
    )
    scored["score"] = fallback["score"]
    scored["score_reasoning"] = _first_non_empty(
        scored.get("score_reasoning"),
        fallback["reasoning"],
    )
    return scored


def _sanitize_lead(lead: dict, session_id: str) -> dict:
    """Ensure lead document has all required fields."""
    fallback = fallback_score_from_lead(lead)
    return {
        "name": _first_non_empty(lead.get("name"), lead.get("person_name")),
        "title": _first_non_empty(lead.get("title"), lead.get("headline")),
        "company": _first_non_empty(lead.get("company"), lead.get("company_name")),
        "email": _first_non_empty(lead.get("email"), lead.get("best_email"), lead.get("emails_found")),
        "phone": _first_non_empty(
            lead.get("phone"),
            lead.get("best_phone"),
            lead.get("phone_numbers"),
            lead.get("contact_number"),
        ),
        "linkedin_url": lead.get("linkedin_url", ""),
        "website": lead.get("website", ""),
        "industry": _first_non_empty(lead.get("industry"), lead.get("category")),
        "company_size": lead.get("company_size", ""),
        "description": lead.get("description", ""),
        "score": _coerce_int(lead.get("score"), fallback["score"]),
        "score_reasoning": _first_non_empty(
            lead.get("score_reasoning"),
            lead.get("reasoning"),
            fallback["reasoning"],
        ),
        "source": lead.get("source", "agent"),
        "session_id": session_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@tool
async def storage(
    leads: str,
    session_id: str = "default",
    original_query: str = "",
) -> str:
    """
    Save leads to the MongoDB database. Call this after enrichment and scoring.

    Args:
        leads: JSON string — either a single lead dict or a list of lead dicts.
               Each lead should have: name, title, company, email, linkedin_url,
               website, score, industry, company_size, source.
        session_id: Session identifier for grouping leads (use the current session_id)
        original_query: The user's original lead request for query-aware scoring

    Returns:
        JSON with count of leads saved and their IDs
    """
    try:
        data = json.loads(leads) if isinstance(leads, str) else leads
        lead_list: list[dict] = data if isinstance(data, list) else [data]

        saved_ids = []
        errors = []

        for raw_lead in lead_list:
            if not raw_lead.get("name"):
                continue
            try:
                scored_lead = await _ensure_score(raw_lead, original_query=original_query)
                clean = _sanitize_lead(scored_lead, session_id)
                doc_id = await upsert_lead(clean)
                saved_ids.append(doc_id)
            except Exception as e:
                errors.append({"lead": raw_lead.get("name"), "error": str(e)})

        return json.dumps({
            "saved_count": len(saved_ids),
            "ids": saved_ids,
            "errors": errors,
            "message": f"Successfully stored {len(saved_ids)} lead(s)",
        })

    except Exception as e:
        logger.error(f"storage tool error: {e}")
        return json.dumps({"error": str(e), "saved_count": 0})
