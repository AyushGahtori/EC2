"""
llm_review.py - Node: llm_review_node

Calls Ollama HTTP API with qwen3-coder:480b-cloud, enforces JSON output,
includes fallback regex extraction and retry logic.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from pr_copilot.config import settings
from pr_copilot.state import (
    BanditIssue,
    DiffChunk,
    Flake8Issue,
    LLMComment,
    PRCopilotState,
)

logger = logging.getLogger(__name__)

# ── Prompt builder ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an expert code reviewer. You will be given a pull request diff, \
static analysis results, and repository guidelines. \
Your task is to produce structured, actionable review comments.

STRICT RULES:
1. Output ONLY valid JSON — no markdown fences, no prose.
2. The JSON must be an array of comment objects.
3. Each comment object must have exactly these fields:
   - "filename": string
   - "line": integer (file line number; 0 = file-level comment)
   - "position": integer (diff position for GitHub inline; 0 if unknown)
   - "severity": one of ["critical", "warning", "suggestion", "info"]
   - "category": one of ["security", "style", "architecture", "logic", "performance"]
   - "message": string (concise review comment)
   - "suggestion": string or null (concrete fix suggestion)
4. Only flag real issues — no noise, no duplicates.
5. If there are no issues, return an empty array: []
"""


def _build_user_prompt(
    chunk: DiffChunk,
    bandit_issues: list[BanditIssue],
    flake8_issues: list[Flake8Issue],
    guidelines: str,
    pr_title: str,
    pr_body: str,
) -> str:
    filename = chunk.filename

    # Filter tool results to this file only
    bandit_for_file = [b for b in bandit_issues if b.filename == filename]
    flake8_for_file = [f for f in flake8_issues if f.filename == filename]

    bandit_text = (
        "\n".join(
            f"  Line {b.line} [{b.severity}/{b.confidence}] {b.test_id}: {b.issue_text}"
            for b in bandit_for_file
        )
        or "  None"
    )
    flake8_text = (
        "\n".join(
            f"  Line {f.line}:{f.col} {f.code}: {f.message}"
            for f in flake8_for_file
        )
        or "  None"
    )

    return f"""## Pull Request Context
Title: {pr_title}
Description: {pr_body[:500] or 'N/A'}

## Repository Guidelines
{guidelines[:1000]}

## File Under Review
{filename} (chunk {chunk.chunk_index}, lines {chunk.start_line}–{chunk.end_line})

## Diff
```diff
{chunk.content}
```

## Bandit Security Issues
{bandit_text}

## Flake8 Style Issues
{flake8_text}

## Task
Review the diff above and return a JSON array of comment objects following the schema in your instructions.
"""


# ── Ollama HTTP client ─────────────────────────────────────────────────────────

def _call_ollama(prompt: str) -> str:
    """
    Call Ollama /api/chat endpoint with strict JSON format enforcement.
    Returns the raw assistant message string.
    """
    url = f"{settings.ollama_base_url}/api/chat"
    payload = {
        "model": settings.ollama_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "format": "json",          # Ollama native JSON mode
        "options": {
            "temperature": 0.1,    # low temperature for deterministic output
            "num_predict": 4096,
        },
    }

    with httpx.Client(timeout=settings.ollama_timeout) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["message"]["content"]


# ── Response parser ────────────────────────────────────────────────────────────

_JSON_ARRAY_RE = re.compile(r"\[.*?\]", re.DOTALL)


def _parse_response(raw: str) -> list[dict[str, Any]]:
    """
    Parse LLM response into a list of comment dicts.

    Strategy:
    1. Direct json.loads (best case — Ollama JSON mode)
    2. Regex extraction of first JSON array
    3. Return empty list (safe fallback)
    """
    raw = raw.strip()

    # 1. Try direct parse
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict) and "comments" in parsed:
            return parsed["comments"]
    except json.JSONDecodeError:
        pass

    # 2. Regex fallback — grab first [...] block
    match = _JSON_ARRAY_RE.search(raw)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    logger.warning("Could not parse LLM response: %s", raw[:200])
    return []


def _validate_comment(raw: dict[str, Any], chunk: DiffChunk) -> LLMComment | None:
    """Validate and coerce a raw comment dict into LLMComment."""
    try:
        line = int(raw.get("line", 0))
        position = int(raw.get("position", 0))

        # If line is given but position is not, look it up from line_map
        if position == 0 and line > 0:
            # Find the diff position for this line number
            for pos, ln in chunk.line_map.items():
                if ln == line:
                    position = pos
                    break

        severity = raw.get("severity", "info")
        if severity not in {"critical", "warning", "suggestion", "info"}:
            severity = "info"

        category = raw.get("category", "logic")
        if category not in {"security", "style", "architecture", "logic", "performance"}:
            category = "logic"

        return LLMComment(
            filename=raw.get("filename", chunk.filename),
            line=line,
            position=position,
            severity=severity,
            category=category,
            message=str(raw.get("message", "")).strip(),
            suggestion=raw.get("suggestion") or None,
        )
    except Exception as exc:
        logger.debug("Comment validation failed: %s | %s", exc, raw)
        return None


# ── Node ──────────────────────────────────────────────────────────────────────

def llm_review_node(state: PRCopilotState) -> PRCopilotState:
    """
    LangGraph node: send diff chunks to Ollama and collect review comments.

    Processes all DiffChunks sequentially (can be parallelised via LangGraph
    fan-out at the graph level if needed). Stores raw response for retry logic.
    """
    if state.error:
        return state

    all_comments: list[LLMComment] = []
    last_raw = ""
    successful_calls = 0

    for chunk in state.diff_chunks:
        prompt = _build_user_prompt(
            chunk=chunk,
            bandit_issues=state.bandit_results,
            flake8_issues=state.flake8_results,
            guidelines=state.repo_guidelines,
            pr_title=state.pr_title,
            pr_body=state.pr_body,
        )

        try:
            raw = _call_ollama(prompt)
            last_raw = raw
            successful_calls += 1
            raw_comments = _parse_response(raw)
            for rc in raw_comments:
                comment = _validate_comment(rc, chunk)
                if comment and comment.message:
                    all_comments.append(comment)

        except httpx.HTTPError as exc:
            logger.error("Ollama HTTP error on chunk %s/%d: %s", chunk.filename, chunk.chunk_index, exc)
            # Don't abort — continue with remaining chunks
        except Exception as exc:
            logger.exception("Unexpected error in llm_review_node for chunk %s/%d", chunk.filename, chunk.chunk_index)

    if state.diff_chunks and successful_calls == 0:
        error_msg = "LLM review failed: no successful responses from Ollama."
        logger.error(error_msg)
        return state.model_copy(
            update={
                "llm_reviews": [],
                "llm_raw_response": last_raw,
                "error": error_msg,
            }
        )

    logger.info("LLM produced %d review comments", len(all_comments))
    return state.model_copy(
        update={
            "llm_reviews": all_comments,
            "llm_raw_response": last_raw,
        }
    )
