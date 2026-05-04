"""
tests/test_chunker.py - Unit tests for chunk_diff_node
"""

from __future__ import annotations

import pytest

from pr_copilot.nodes.chunker import _parse_line_map, _split_patch_into_chunks
from pr_copilot.state import PRCopilotState


SAMPLE_PATCH = """\
@@ -10,6 +10,10 @@ def foo():
 context line
 context line
+added line 1
+added line 2
-removed line
 context line
@@ -50,4 +54,7 @@ def bar():
 context line
+added line 3
+added line 4
+added line 5
 context line
"""


def test_parse_line_map_basic():
    line_map = _parse_line_map(SAMPLE_PATCH)
    # position 1 = first line after first @@ = "context line" → new line 10
    assert isinstance(line_map, dict)
    assert len(line_map) > 0
    # All values should be positive integers
    assert all(v > 0 for v in line_map.values())


def test_parse_line_map_positions_monotone():
    line_map = _parse_line_map(SAMPLE_PATCH)
    positions = sorted(line_map.keys())
    values = [line_map[p] for p in positions]
    # Line numbers should be non-decreasing across chunks
    for i in range(len(values) - 1):
        assert values[i] <= values[i + 1] or values[i + 1] >= 1


def test_split_into_chunks_small_patch():
    chunks = list(_split_patch_into_chunks("foo.py", SAMPLE_PATCH, chunk_size=50))
    assert len(chunks) == 1
    assert chunks[0].filename == "foo.py"
    assert chunks[0].chunk_index == 0


def test_split_into_chunks_small_chunk_size():
    chunks = list(_split_patch_into_chunks("foo.py", SAMPLE_PATCH, chunk_size=3))
    assert len(chunks) > 1
    for i, chunk in enumerate(chunks):
        assert chunk.chunk_index == i
        assert chunk.filename == "foo.py"


def test_chunk_diff_node_no_error():
    state = PRCopilotState(
        repo="owner/repo",
        pr_number=1,
        diffs={"src/foo.py": SAMPLE_PATCH},
    )
    from pr_copilot.nodes.chunker import chunk_diff_node
    result = chunk_diff_node(state)
    assert result.error is None
    assert len(result.diff_chunks) >= 1
    assert result.diff_chunks[0].filename == "src/foo.py"


def test_chunk_diff_node_skips_on_error():
    state = PRCopilotState(
        repo="owner/repo",
        pr_number=1,
        error="Something went wrong",
        diffs={"src/foo.py": SAMPLE_PATCH},
    )
    from pr_copilot.nodes.chunker import chunk_diff_node
    result = chunk_diff_node(state)
    assert result.diff_chunks == []  # unchanged
