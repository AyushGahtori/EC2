"""
MongoDB async client for lead persistence.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config.settings import settings

logger = logging.getLogger(__name__)

_mongo_client: Optional[AsyncIOMotorClient] = None


def get_mongo_client() -> AsyncIOMotorClient:
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = AsyncIOMotorClient(settings.MONGODB_URI)
    return _mongo_client


def get_db() -> AsyncIOMotorDatabase:
    return get_mongo_client()[settings.MONGODB_DB]


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


def _fallback_score_from_lead(lead: dict[str, Any]) -> dict[str, Any]:
    """Local fallback used to normalize legacy unscored documents."""
    title = _first_non_empty(lead.get("title"), lead.get("headline")).lower()
    senior_hints = (
        "founder",
        "co-founder",
        "ceo",
        "chief",
        "cto",
        "cfo",
        "coo",
        "president",
        "owner",
        "partner",
        "director",
        "head",
        "vp",
        "vice president",
    )

    score = 20
    if title:
        score += 12
    if any(hint in title for hint in senior_hints):
        score += 18
    if _first_non_empty(lead.get("company"), lead.get("company_name")):
        score += 10
    if _first_non_empty(lead.get("industry"), lead.get("category")):
        score += 8
    if _first_non_empty(lead.get("website")):
        score += 8
    if _first_non_empty(lead.get("linkedin_url")):
        score += 12
    if _first_non_empty(lead.get("email"), lead.get("best_email")):
        score += 18
    if _first_non_empty(lead.get("phone"), lead.get("best_phone")):
        score += 6
    if _first_non_empty(lead.get("description")):
        score += 5
    if _first_non_empty(lead.get("company_size")):
        score += 3

    return {
        "score": min(score, 100),
        "reasoning": "Scored from lead completeness and seniority signals",
    }


def _normalize_lead_document(doc: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy and tool-shaped fields into the UI/API contract."""
    normalized = dict(doc)
    normalized["name"] = _first_non_empty(doc.get("name"), doc.get("person_name"))
    normalized["title"] = _first_non_empty(doc.get("title"), doc.get("headline"))
    normalized["company"] = _first_non_empty(doc.get("company"), doc.get("company_name"))
    normalized["email"] = _first_non_empty(
        doc.get("email"),
        doc.get("best_email"),
        doc.get("emails_found"),
        doc.get("email_patterns"),
    )
    normalized["phone"] = _first_non_empty(
        doc.get("phone"),
        doc.get("best_phone"),
        doc.get("phone_numbers"),
        doc.get("contact_number"),
    )
    normalized["industry"] = _first_non_empty(doc.get("industry"), doc.get("category"))
    normalized["linkedin_url"] = _first_non_empty(doc.get("linkedin_url"), doc.get("linkedin"))
    normalized["website"] = _first_non_empty(doc.get("website"), doc.get("domain"))
    score_reasoning = _first_non_empty(doc.get("score_reasoning"), doc.get("reasoning"))
    fallback = _fallback_score_from_lead(normalized)
    try:
        raw_score = int(doc.get("score", 0) or 0)
    except (TypeError, ValueError):
        raw_score = 0
    normalized["score"] = fallback["score"] if raw_score == 0 or (raw_score == 50 and not score_reasoning) else raw_score
    normalized["score_reasoning"] = score_reasoning or fallback["reasoning"]
    return normalized


async def insert_lead(lead: dict) -> str:
    """Insert a single lead document. Returns the inserted ID."""
    try:
        db = get_db()
        result = await db.leads.insert_one(lead)
        return str(result.inserted_id)
    except Exception as e:
        logger.error(f"MongoDB insert_lead failed: {e}")
        raise


async def upsert_lead(lead: dict) -> str:
    """
    Upsert a lead by (name + company) or linkedin_url.
    Prevents duplicates across agent runs.
    """
    try:
        db = get_db()
        filter_q = {}
        if lead.get("linkedin_url"):
            filter_q = {"linkedin_url": lead["linkedin_url"]}
        elif lead.get("name") and lead.get("company"):
            filter_q = {"name": lead["name"], "company": lead["company"]}

        if not filter_q:
            result = await db.leads.insert_one(lead)
            return str(result.inserted_id)

        created_at = lead.get("created_at")
        set_payload = {k: v for k, v in lead.items() if k != "created_at"}
        update_doc: dict[str, Any] = {"$set": set_payload}
        if created_at:
            update_doc["$setOnInsert"] = {"created_at": created_at}

        result = await db.leads.update_one(
            filter_q, update_doc, upsert=True
        )
        if result.upserted_id:
            return str(result.upserted_id)
        doc = await db.leads.find_one(filter_q, {"_id": 1})
        return str(doc["_id"]) if doc else ""
    except Exception as e:
        logger.error(f"MongoDB upsert_lead failed: {e}")
        raise


async def get_leads(
    session_id: Optional[str] = None,
    limit: int = 100,
    skip: int = 0,
) -> list[dict]:
    """Fetch leads, optionally filtered by session."""
    try:
        db = get_db()
        query: dict[str, Any] = {}
        if session_id:
            query["session_id"] = session_id
        cursor = db.leads.find(query, {"_id": 0}).sort("score", -1).skip(skip).limit(limit)
        docs = await cursor.to_list(length=limit)
        return [_normalize_lead_document(doc) for doc in docs]
    except Exception as e:
        logger.error(f"MongoDB get_leads failed: {e}")
        return []


async def count_leads(session_id: Optional[str] = None) -> int:
    try:
        db = get_db()
        query: dict[str, Any] = {}
        if session_id:
            query["session_id"] = session_id
        return await db.leads.count_documents(query)
    except Exception as e:
        logger.error(f"MongoDB count_leads failed: {e}")
        return 0
