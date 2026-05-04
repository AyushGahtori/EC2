"""
post_comments.py - Node: post_comments_node

Posts inline review comments and a summary review body to GitHub
using the Pull Request Review API.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from pr_copilot.config import settings
from pr_copilot.state import LLMComment, PRCopilotState

logger = logging.getLogger(__name__)

GITHUB_HEADERS = {
    "Authorization": f"Bearer {settings.github_token}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# Severity → GitHub body emoji prefix
SEVERITY_EMOJI = {
    "critical":   "🔴 **CRITICAL**",
    "warning":    "🟡 **Warning**",
    "suggestion": "💡 **Suggestion**",
    "info":       "ℹ️ **Info**",
}

CATEGORY_LABEL = {
    "security":     "[Security]",
    "style":        "[Style]",
    "architecture": "[Architecture]",
    "logic":        "[Logic]",
    "performance":  "[Performance]",
}


def _format_comment_body(comment: LLMComment) -> str:
    severity_tag = SEVERITY_EMOJI.get(comment.severity, "")
    category_tag = CATEGORY_LABEL.get(comment.category, "")
    body = f"{severity_tag} {category_tag}\n\n{comment.message}"
    if comment.suggestion:
        body += f"\n\n**Suggested fix:**\n```python\n{comment.suggestion}\n```"
    return body


def _build_review_comments(
    comments: list[LLMComment],
    valid_positions_by_file: dict[str, set[int]],
) -> tuple[list[dict[str, Any]], list[LLMComment]]:
    """
    Build the `comments` array for GitHub's Create Review API.
    Only includes comments with a valid diff position (> 0).
    File-level comments (line == 0) become review body text instead.
    """
    inline: list[dict[str, Any]] = []
    downgraded_to_file_level: list[LLMComment] = []
    for c in comments:
        if c.position <= 0:
            continue

        valid_positions = valid_positions_by_file.get(c.filename, set())
        if c.position in valid_positions:
            inline.append(
                {
                    "path": c.filename,
                    "position": c.position,
                    "body": _format_comment_body(c),
                }
            )
        else:
            # Keep the feedback, but move it to review body so GitHub won't reject.
            downgraded_to_file_level.append(c.model_copy(update={"position": 0}))
    return inline, downgraded_to_file_level


def _build_file_level_section(comments: list[LLMComment]) -> str:
    """Collect file-level (line == 0) comments into the review body."""
    file_comments = [c for c in comments if c.line == 0 or c.position == 0]
    if not file_comments:
        return ""
    lines = ["\n\n---\n### 📋 File-level Comments\n"]
    for c in file_comments:
        prefix = SEVERITY_EMOJI.get(c.severity, "")
        cat = CATEGORY_LABEL.get(c.category, "")
        lines.append(f"- **{c.filename}** {prefix} {cat}: {c.message}")
    return "\n".join(lines)


def post_comments_node(state: PRCopilotState) -> PRCopilotState:
    """
    LangGraph node: post a GitHub Pull Request Review with inline comments.

    Uses the `POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews` endpoint
    which atomically creates a review + all inline comments in one request.

    If `has_issues` is False, posts an approval review instead.
    """
    if state.error:
        return state

    if state.skipped:
        logger.info("Skipping post_comments_node — no issues found.")
        return state

    repo = state.repo
    pr_number = state.pr_number
    url = f"{settings.github_api_base}/repos/{repo}/pulls/{pr_number}/reviews"

    valid_positions_by_file: dict[str, set[int]] = {}
    for chunk in state.diff_chunks:
        valid_positions_by_file.setdefault(chunk.filename, set()).update(chunk.line_map.keys())

    inline_comments, downgraded_comments = _build_review_comments(
        state.final_comments, valid_positions_by_file
    )
    if downgraded_comments:
        logger.warning(
            "Downgraded %d inline comments to file-level due to unresolved diff positions.",
            len(downgraded_comments),
        )

    file_level_section = _build_file_level_section(state.final_comments + downgraded_comments)

    review_body = (
        f"## 🤖 PR Copilot Review\n\n{state.review_summary}{file_level_section}"
    )

    # Decide review event
    if not state.has_issues:
        event = "APPROVE"
        review_body = f"## 🤖 PR Copilot Review\n\n✅ {state.review_summary}"
        inline_comments = []
    elif any(c.severity == "critical" for c in state.final_comments):
        event = "REQUEST_CHANGES"
    else:
        event = "COMMENT"

    payload: dict[str, Any] = {
        "commit_id": state.head_sha,
        "body": review_body,
        "event": event,
        "comments": inline_comments,
    }

    try:
        with httpx.Client(headers=GITHUB_HEADERS, timeout=30) as client:
            resp = client.post(url, json=payload)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError:
                lowered = resp.text.lower()
                own_pr_blocked = resp.status_code == 422 and (
                    "request changes on your own pull request" in lowered
                    or "approve your own pull request" in lowered
                )
                unresolved_position = (
                    resp.status_code == 422
                    and "position could not be resolved" in lowered
                    and bool(inline_comments)
                )
                # GitHub forbids APPROVE/REQUEST_CHANGES on your own PR.
                if (
                    own_pr_blocked
                    and event in {"REQUEST_CHANGES", "APPROVE"}
                ):
                    logger.warning(
                        "GitHub rejected %s on own PR; retrying as COMMENT.",
                        event,
                    )
                    fallback_payload = payload.copy()
                    fallback_payload["event"] = "COMMENT"
                    resp = client.post(url, json=fallback_payload)
                    resp.raise_for_status()
                    event = "COMMENT"
                elif unresolved_position:
                    logger.warning(
                        "GitHub rejected inline positions; retrying review without inline comments."
                    )
                    fallback_payload = payload.copy()
                    fallback_payload["comments"] = []
                    resp = client.post(url, json=fallback_payload)
                    resp.raise_for_status()
                    inline_comments = []
                else:
                    raise

            review_id = resp.json().get("id")
            logger.info(
                "Posted GitHub review #%s on PR #%s (%s inline comments, event=%s)",
                review_id, pr_number, len(inline_comments), event,
            )
    except httpx.HTTPStatusError as exc:
        logger.error("Failed to post GitHub review: %s", exc.response.text)
        return state.model_copy(update={"error": f"GitHub post error: {exc}"})
    except Exception as exc:
        logger.exception("Unexpected error posting review")
        return state.model_copy(update={"error": str(exc)})

    return state
