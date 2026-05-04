"""
chunker.py - Node: chunk_diff_node

Splits large diffs into manageable chunks and builds diff-position →
file-line-number mapping so GitHub inline comments land on the right line.
"""

from __future__ import annotations

import logging
import re
from typing import Generator

from pr_copilot.config import settings
from pr_copilot.state import DiffChunk, PRCopilotState

logger = logging.getLogger(__name__)

# Regex to parse unified diff hunk headers:  @@ -L,S +L,S @@
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _parse_line_map(patch: str) -> dict[int, int]:
    """
    Build a mapping from diff *position* (1-indexed line in the patch blob
    as GitHub counts it) to the *new* file line number.

    GitHub's diff position is the cumulative line offset from the first @@
    hunk header, incrementing for every line (context, +, -) but NOT for
    hunk header lines themselves.
    """
    line_map: dict[int, int] = {}
    position = 0
    new_line = 0

    for raw_line in patch.splitlines():
        if raw_line.startswith("@@"):
            m = _HUNK_RE.match(raw_line)
            if m:
                new_line = int(m.group(2)) - 1   # will be incremented below
            # hunk headers do NOT increment position in GitHub's scheme
            continue

        position += 1

        if raw_line.startswith("-"):
            pass  # deleted line — no new-file line number
        else:
            new_line += 1
            line_map[position] = new_line

    return line_map


def _split_patch_into_chunks(
    filename: str,
    patch: str,
    chunk_size: int,
) -> Generator[DiffChunk, None, None]:
    """
    Yield DiffChunk objects, each containing at most `chunk_size` lines.
    Chunk boundaries respect hunk headers so we never split mid-hunk if
    possible.
    """
    lines = patch.splitlines(keepends=True)
    chunk_lines: list[str] = []
    chunk_index = 0
    position = 0
    new_line = 0
    chunk_start_line = 1
    line_map: dict[int, int] = {}

    def flush_chunk(current_index: int, start_line: int) -> DiffChunk:
        content = "".join(chunk_lines)
        end_line = new_line
        return DiffChunk(
            filename=filename,
            chunk_index=current_index,
            content=content,
            start_line=start_line,
            end_line=end_line,
            line_map=line_map.copy(),
        )

    for raw_line in lines:
        stripped = raw_line.rstrip("\n")

        if stripped.startswith("@@"):
            # If buffer full, flush before starting a new hunk
            if len(chunk_lines) >= chunk_size:
                yield flush_chunk(chunk_index, chunk_start_line)
                chunk_index += 1
                chunk_start_line = new_line + 1
                chunk_lines = []
                line_map = {}

            m = _HUNK_RE.match(stripped)
            if m:
                new_line = int(m.group(2)) - 1
            chunk_lines.append(raw_line)
            continue

        position += 1
        if not stripped.startswith("-"):
            new_line += 1
            line_map[position] = new_line

        chunk_lines.append(raw_line)

        if len(chunk_lines) >= chunk_size:
            yield flush_chunk(chunk_index, chunk_start_line)
            chunk_index += 1
            chunk_start_line = new_line + 1
            chunk_lines = []
            line_map = {}

    if chunk_lines:
        yield flush_chunk(chunk_index, chunk_start_line)


def _total_diff_lines(diffs: dict[str, str]) -> int:
    return sum(len(patch.splitlines()) for patch in diffs.values())


def chunk_diff_node(state: PRCopilotState) -> PRCopilotState:
    """
    LangGraph node: chunk large diffs.

    If the total diff line count exceeds `settings.diff_chunk_threshold`,
    split each file's patch into DiffChunks; otherwise create one chunk
    per file (pass-through).
    """
    if state.error:
        return state

    chunks: list[DiffChunk] = []
    total_lines = _total_diff_lines(state.diffs)
    is_large = total_lines > settings.diff_chunk_threshold

    logger.info(
        "Diff total lines: %d | chunking: %s", total_lines, is_large
    )

    for filename, patch in state.diffs.items():
        if not patch:
            continue
        if is_large:
            for chunk in _split_patch_into_chunks(
                filename, patch, settings.diff_chunk_size
            ):
                chunks.append(chunk)
        else:
            # Single chunk per file with full line map
            line_map = _parse_line_map(patch)
            patch_lines = patch.splitlines()
            end_line = max(line_map.values(), default=0)
            chunks.append(
                DiffChunk(
                    filename=filename,
                    chunk_index=0,
                    content=patch,
                    start_line=1,
                    end_line=end_line,
                    line_map=line_map,
                )
            )

    logger.info("Created %d diff chunks from %d files", len(chunks), len(state.diffs))
    return state.model_copy(update={"diff_chunks": chunks, "needs_chunking": is_large})
