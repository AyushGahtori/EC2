"""
Lead scoring tool that scores leads against the user's ICP.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from app.agent.prompts import LEAD_SCORING_PROMPT
from app.services.llm_provider import get_llm

logger = logging.getLogger(__name__)

SENIOR_TITLE_HINTS = (
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


def _safe_parse_json(text: str) -> dict:
    """Extract JSON from LLM output robustly."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    inline = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if inline:
        try:
            return json.loads(inline.group(0))
        except json.JSONDecodeError:
            pass

    return {}


def fallback_score_from_lead(lead: dict[str, Any], reason: str = "") -> dict[str, Any]:
    """Deterministic fallback score based on lead quality and seniority signals."""
    title = str(lead.get("title", "") or lead.get("headline", "")).lower()

    score = 20
    if lead.get("title") or lead.get("headline"):
        score += 12
    if any(hint in title for hint in SENIOR_TITLE_HINTS):
        score += 18
    if lead.get("company") or lead.get("company_name"):
        score += 10
    if lead.get("industry") or lead.get("category"):
        score += 8
    if lead.get("website"):
        score += 8
    if lead.get("linkedin_url"):
        score += 12
    if lead.get("email") or lead.get("best_email"):
        score += 18
    if lead.get("phone") or lead.get("best_phone"):
        score += 6
    if lead.get("description"):
        score += 5
    if lead.get("company_size"):
        score += 3

    return {
        "score": min(score, 100),
        "reasoning": reason or "Scored from lead completeness and seniority signals",
        "strengths": [],
        "gaps": [],
    }


@tool
async def lead_scoring(
    lead_data: str,
    original_query: str,
) -> str:
    """
    Score a lead from 0 to 100 based on how well they match the ICP in the query.

    Args:
        lead_data: JSON string containing lead information.
        original_query: The original user query describing the target persona/ICP.

    Returns:
        JSON with score, reasoning, strengths, and gaps.
    """
    lead: dict[str, Any] = {}

    try:
        if isinstance(lead_data, str):
            lead = json.loads(lead_data)
        else:
            lead = lead_data

        prompt = LEAD_SCORING_PROMPT.format(
            original_query=original_query,
            name=lead.get("name", "Unknown"),
            title=lead.get("title", ""),
            company=lead.get("company", ""),
            industry=lead.get("industry", ""),
            company_size=lead.get("company_size", ""),
            website=lead.get("website", ""),
            linkedin_url=lead.get("linkedin_url", ""),
            has_email="Yes" if lead.get("email") else "No",
            description=lead.get("description", ""),
        )

        llm = get_llm(streaming=False)
        response = await llm.ainvoke(
            [
                SystemMessage(content="You are an expert lead scoring analyst. Return ONLY valid JSON."),
                HumanMessage(content=prompt),
            ]
        )
        result_raw = response.content if hasattr(response, "content") else str(response)
        scoring = _safe_parse_json(result_raw)

        if not scoring or "score" not in scoring:
            scoring = fallback_score_from_lead(
                lead,
                reason="Scored from completeness signals because LLM output was not valid JSON",
            )

        return json.dumps(
            {
                "lead_name": lead.get("name"),
                "score": int(scoring.get("score", 0)),
                "reasoning": scoring.get("reasoning", ""),
                "strengths": scoring.get("strengths", []),
                "gaps": scoring.get("gaps", []),
            }
        )

    except Exception as exc:
        logger.error(f"lead_scoring error: {exc}")
        fallback = fallback_score_from_lead(
            lead,
            reason=f"Scoring fallback used after tool error: {exc}",
        )
        return json.dumps(
            {
                "error": str(exc),
                "score": fallback["score"],
                "reasoning": fallback["reasoning"],
                "strengths": fallback["strengths"],
                "gaps": fallback["gaps"],
            }
        )
