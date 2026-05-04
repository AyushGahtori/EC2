"""
state.py - LangGraph State Schema for PR Copilot
Defines the shared state object passed between all nodes.
"""

from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field


class PRFile(BaseModel):
    filename: str
    status: str          # added | modified | removed | renamed
    additions: int
    deletions: int
    patch: Optional[str] = None   # raw unified diff
    sha: str = ""
    blob_url: str = ""


class DiffChunk(BaseModel):
    filename: str
    chunk_index: int
    content: str          # portion of the unified diff
    start_line: int       # first line of original file in chunk
    end_line: int         # last line of original file in chunk
    line_map: dict[int, int] = Field(default_factory=dict)
    # line_map: diff_position -> original file line number


class LLMComment(BaseModel):
    filename: str
    line: int             # file line number (0 = file-level)
    position: int         # GitHub diff position (for inline)
    severity: str         # critical | warning | suggestion | info
    category: str         # security | style | architecture | logic
    message: str
    suggestion: Optional[str] = None


class BanditIssue(BaseModel):
    filename: str
    line: int
    severity: str
    confidence: str
    test_id: str
    issue_text: str
    code: str = ""


class Flake8Issue(BaseModel):
    filename: str
    line: int
    col: int
    code: str
    message: str


class PRCopilotState(BaseModel):
    # ── PR identity ──────────────────────────────────────────────────────────
    repo: str = ""                          # "owner/repo"
    pr_number: int = 0
    base_sha: str = ""
    head_sha: str = ""
    pr_title: str = ""
    pr_body: str = ""

    # ── Fetched data ─────────────────────────────────────────────────────────
    files: list[PRFile] = Field(default_factory=list)
    diffs: dict[str, str] = Field(default_factory=dict)     # filename -> raw patch

    # ── Chunking ─────────────────────────────────────────────────────────────
    diff_chunks: list[DiffChunk] = Field(default_factory=list)
    needs_chunking: bool = False

    # ── Tool results ─────────────────────────────────────────────────────────
    bandit_results: list[BanditIssue] = Field(default_factory=list)
    flake8_results: list[Flake8Issue] = Field(default_factory=list)

    # ── LLM review ───────────────────────────────────────────────────────────
    llm_reviews: list[LLMComment] = Field(default_factory=list)
    llm_retry_count: int = 0
    llm_raw_response: str = ""

    # ── Final output ─────────────────────────────────────────────────────────
    final_comments: list[LLMComment] = Field(default_factory=list)
    review_summary: str = ""
    has_issues: bool = False

    # ── Control flow ─────────────────────────────────────────────────────────
    error: Optional[str] = None
    skipped: bool = False

    # ── Repo guidelines (injected at startup) ────────────────────────────────
    repo_guidelines: str = ""

    class Config:
        arbitrary_types_allowed = True
