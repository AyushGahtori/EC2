"""
tests/test_llm_review.py - Unit tests for LLM response parser
"""

from __future__ import annotations

import json

import pytest

from pr_copilot.nodes.llm_review import _parse_response, _validate_comment
from pr_copilot.state import DiffChunk


SAMPLE_CHUNK = DiffChunk(
    filename="src/auth.py",
    chunk_index=0,
    content="@@ -1,5 +1,8 @@\n+import os\n+password = os.getenv('PASSWORD')\n",
    start_line=1,
    end_line=8,
    line_map={1: 1, 2: 2},
)


def make_valid_raw():
    return {
        "filename": "src/auth.py",
        "line": 2,
        "position": 2,
        "severity": "critical",
        "category": "security",
        "message": "Hardcoded password detected",
        "suggestion": "Use a secrets manager instead",
    }


# ── _parse_response ───────────────────────────────────────────────────────────

def test_parse_direct_array():
    raw = json.dumps([make_valid_raw()])
    result = _parse_response(raw)
    assert len(result) == 1
    assert result[0]["severity"] == "critical"


def test_parse_wrapped_dict():
    raw = json.dumps({"comments": [make_valid_raw()]})
    result = _parse_response(raw)
    assert len(result) == 1


def test_parse_markdown_fenced():
    inner = json.dumps([make_valid_raw()])
    raw = f"```json\n{inner}\n```"
    result = _parse_response(raw)
    assert len(result) == 1


def test_parse_garbage_returns_empty():
    result = _parse_response("This is not JSON at all.")
    assert result == []


def test_parse_empty_array():
    result = _parse_response("[]")
    assert result == []


# ── _validate_comment ─────────────────────────────────────────────────────────

def test_validate_comment_good():
    comment = _validate_comment(make_valid_raw(), SAMPLE_CHUNK)
    assert comment is not None
    assert comment.severity == "critical"
    assert comment.category == "security"
    assert comment.position == 2


def test_validate_comment_bad_severity_defaults():
    raw = make_valid_raw()
    raw["severity"] = "UNKNOWN_SEVERITY"
    comment = _validate_comment(raw, SAMPLE_CHUNK)
    assert comment is not None
    assert comment.severity == "info"


def test_validate_comment_line_lookup():
    """If position is 0 but line is given, look up position from line_map."""
    raw = make_valid_raw()
    raw["position"] = 0
    raw["line"] = 2
    comment = _validate_comment(raw, SAMPLE_CHUNK)
    assert comment is not None
    assert comment.position == 2  # from line_map: {1:1, 2:2}


def test_validate_comment_empty_message_allowed():
    raw = make_valid_raw()
    raw["message"] = ""
    comment = _validate_comment(raw, SAMPLE_CHUNK)
    # message is empty — comment should still be created (filtering is in node)
    assert comment is not None
    assert comment.message == ""
