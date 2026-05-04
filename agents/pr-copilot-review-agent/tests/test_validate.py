"""
tests/test_validate.py - Unit tests for validate_output_node
"""

from __future__ import annotations

import pytest

from pr_copilot.nodes.validate import validate_output_node, _deduplicate
from pr_copilot.state import LLMComment, PRCopilotState


def make_comment(**kwargs) -> LLMComment:
    defaults = dict(
        filename="src/foo.py",
        line=10,
        position=3,
        severity="warning",
        category="style",
        message="Use snake_case for variable names",
        suggestion=None,
    )
    defaults.update(kwargs)
    return LLMComment(**defaults)


def test_deduplicate_removes_exact_dups():
    c = make_comment()
    result = _deduplicate([c, c, c])
    assert len(result) == 1


def test_deduplicate_keeps_different():
    c1 = make_comment(line=10)
    c2 = make_comment(line=20)
    result = _deduplicate([c1, c2])
    assert len(result) == 2


def test_validate_passes_good_output():
    comments = [make_comment(), make_comment(line=20, message="Missing docstring")]
    state = PRCopilotState(
        repo="owner/repo", pr_number=1, llm_reviews=comments
    )
    result = validate_output_node(state)
    assert result.has_issues is True
    assert len(result.final_comments) == 2
    assert "warning" in result.review_summary.lower() or "🟡" in result.review_summary


def test_validate_no_issues_sets_lgtm():
    state = PRCopilotState(repo="owner/repo", pr_number=1, llm_reviews=[])
    result = validate_output_node(state)
    assert result.has_issues is False
    assert "✅" in result.review_summary


def test_validate_invalid_comment_triggers_retry():
    bad_comment = make_comment(message="")   # empty message = invalid
    state = PRCopilotState(
        repo="owner/repo",
        pr_number=1,
        llm_reviews=[bad_comment],
        llm_retry_count=0,
    )
    result = validate_output_node(state)
    # Should increment retry count
    assert result.llm_retry_count == 1


def test_validate_exhausted_retries_uses_empty():
    bad_comment = make_comment(message="")
    state = PRCopilotState(
        repo="owner/repo",
        pr_number=1,
        llm_reviews=[bad_comment],
        llm_retry_count=2,  # already at max
    )
    result = validate_output_node(state)
    assert result.final_comments == []
    assert "⚠️" in result.review_summary


def test_validate_skips_on_error():
    state = PRCopilotState(
        repo="owner/repo",
        pr_number=1,
        error="upstream failure",
        llm_reviews=[make_comment()],
    )
    result = validate_output_node(state)
    # Node should return unchanged when error present
    assert result.final_comments == []
