"""
validate.py - Node: validate_output_node

Validates LLM output and decides whether to retry or proceed.
Also deduplicates comments and sets `has_issues`.
"""

from __future__ import annotations

import logging

from pr_copilot.config import settings
from pr_copilot.state import LLMComment, PRCopilotState

logger = logging.getLogger(__name__)


def _deduplicate(comments: list[LLMComment]) -> list[LLMComment]:
    """
    Remove comments with identical (filename, line, message[:80]) tuples.
    Preserves first occurrence.
    """
    seen: set[tuple[str, int, str]] = set()
    out: list[LLMComment] = []
    for c in comments:
        key = (c.filename, c.line, c.message[:80])
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _is_output_valid(comments: list[LLMComment]) -> bool:
    """
    Basic structural validation:
    - Each comment must have a non-empty message
    - filename must be a non-empty string
    """
    if comments is None:
        return False
    for c in comments:
        if not c.message or not c.filename:
            return False
    return True


def validate_output_node(state: PRCopilotState) -> PRCopilotState:
    """
    LangGraph node: validate LLM review output.

    Decisions:
    - If output invalid AND retry_count < max_retries → signal retry
    - If output valid or retries exhausted → proceed with best available data
    - Deduplicates comments
    - Sets has_issues flag
    """
    if state.error:
        return state

    reviews = state.llm_reviews

    if not _is_output_valid(reviews):
        if state.llm_retry_count < settings.llm_max_retries:
            logger.warning(
                "LLM output invalid — scheduling retry %d/%d",
                state.llm_retry_count + 1,
                settings.llm_max_retries,
            )
            return state.model_copy(
                update={"llm_retry_count": state.llm_retry_count + 1}
            )
        else:
            logger.error(
                "LLM output still invalid after %d retries. Proceeding with empty comments.",
                settings.llm_max_retries,
            )
            return state.model_copy(
                update={
                    "final_comments": [],
                    "has_issues": False,
                    "review_summary": "⚠️ LLM failed to produce valid output after retries.",
                }
            )

    # Valid output path
    deduped = _deduplicate(reviews)

    # Severity ordering for summary
    critical = [c for c in deduped if c.severity == "critical"]
    warnings  = [c for c in deduped if c.severity == "warning"]
    suggestions = [c for c in deduped if c.severity == "suggestion"]
    info = [c for c in deduped if c.severity == "info"]

    summary_parts = [
        f"🔴 {len(critical)} critical" if critical else "",
        f"🟡 {len(warnings)} warnings" if warnings else "",
        f"💡 {len(suggestions)} suggestions" if suggestions else "",
        f"ℹ️ {len(info)} info" if info else "",
    ]
    summary = "PR Review: " + " | ".join(p for p in summary_parts if p)
    if not deduped:
        summary = "✅ No issues found — LGTM!"

    has_issues = bool(critical or warnings or suggestions)

    logger.info("Validation passed: %d comments (deduped from %d)", len(deduped), len(reviews))

    return state.model_copy(
        update={
            "final_comments": deduped,
            "has_issues": has_issues,
            "review_summary": summary,
        }
    )
