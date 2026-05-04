"""
graph.py - LangGraph construction for PR Copilot

Builds the full StateGraph with:
  - Parallel bandit + flake8 execution
  - Conditional chunking edge
  - LLM retry loop (max 2 retries)
  - Skip commenting if no issues
"""

from __future__ import annotations

import logging
from typing import Literal

from langgraph.graph import END, START, StateGraph

from pr_copilot.config import settings
from pr_copilot.nodes.chunker import chunk_diff_node
from pr_copilot.nodes.fetch_pr import fetch_pr_node
from pr_copilot.nodes.llm_review import llm_review_node
from pr_copilot.nodes.post_comments import post_comments_node
from pr_copilot.nodes.tools import run_bandit_node, run_flake8_node
from pr_copilot.nodes.validate import validate_output_node
from pr_copilot.state import PRCopilotState

logger = logging.getLogger(__name__)


# ── Conditional edge functions ────────────────────────────────────────────────

def route_after_fetch(
    state: PRCopilotState,
) -> list[str] | str:
    """Abort early if fetch failed, otherwise fan out to both tool nodes."""
    if state.error:
        logger.error("Fetch failed: %s — aborting graph.", state.error)
        return END
    if not state.diffs:
        logger.info("No Python diffs found — nothing to review.")
        return END
    return ["run_bandit_node", "run_flake8_node"]


def route_after_validate(
    state: PRCopilotState,
) -> Literal["llm_review_node", "post_comments_node", "skip_end"]:
    """
    After validation:
    - Retry LLM if invalid and retries remain
    - Skip posting if no issues
    - Otherwise post comments
    """
    # Retry path — validate_output_node already incremented retry_count
    if (
        not state.final_comments
        and not state.has_issues
        and not state.review_summary.startswith("✅")
        and not state.review_summary.startswith("⚠️")
        and state.llm_retry_count <= settings.llm_max_retries
        and not state.error
    ):
        # final_comments is empty but NOT because LGTM → retry
        if state.llm_retry_count > 0:
            logger.info("Retrying LLM (attempt %d)", state.llm_retry_count)
            return "llm_review_node"

    if state.error:
        return "skip_end"

    if not state.has_issues:
        logger.info("No issues found — posting summary review.")
        return "post_comments_node"

    return "post_comments_node"


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    """
    Construct and compile the PR Copilot LangGraph.

    Node execution order:
    ┌─────────────────┐
    │  fetch_pr_node  │
    └────────┬────────┘
             │ route_after_fetch
    ┌────────▼────────────────────────────────┐
    │  run_bandit_node ║ run_flake8_node       │  ← parallel fan-out
    └────────┬────────────────────────────────┘
             │ (join)
    ┌────────▼────────┐
    │ chunk_diff_node  │
    └────────┬────────┘
             │
    ┌────────▼────────┐
    │ llm_review_node  │ ◄──────────────────┐
    └────────┬────────┘                      │ retry
             │                               │
    ┌────────▼───────────┐                  │
    │ validate_output_node│ ─────────────────┘
    └────────┬────────────┘
             │ route_after_validate
    ┌────────▼────────┐
    │ post_comments_node│
    └────────┬────────┘
             │
           END
    """
    graph = StateGraph(PRCopilotState)

    # ── Register nodes ────────────────────────────────────────────────────────
    graph.add_node("fetch_pr_node", fetch_pr_node)
    graph.add_node("run_bandit_node", run_bandit_node)
    graph.add_node("run_flake8_node", run_flake8_node)
    graph.add_node("chunk_diff_node", chunk_diff_node)
    graph.add_node("llm_review_node", llm_review_node)
    graph.add_node("validate_output_node", validate_output_node)
    graph.add_node("post_comments_node", post_comments_node)

    # ── Entry edge ────────────────────────────────────────────────────────────
    graph.add_edge(START, "fetch_pr_node")

    # ── After fetch: conditional abort or fan-out to parallel tools ───────────
    graph.add_conditional_edges("fetch_pr_node", route_after_fetch)

    # ── Join parallel branches → chunk ────────────────────────────────────────
    graph.add_edge(["run_bandit_node", "run_flake8_node"], "chunk_diff_node")

    # ── Chunk → LLM ──────────────────────────────────────────────────────────
    graph.add_edge("chunk_diff_node", "llm_review_node")

    # ── LLM → Validate ───────────────────────────────────────────────────────
    graph.add_edge("llm_review_node", "validate_output_node")

    # ── Validate: retry loop or post ─────────────────────────────────────────
    graph.add_conditional_edges(
        "validate_output_node",
        route_after_validate,
        {
            "llm_review_node":    "llm_review_node",
            "post_comments_node": "post_comments_node",
            "skip_end":           END,
        },
    )

    # ── Post → END ───────────────────────────────────────────────────────────
    graph.add_edge("post_comments_node", END)

    return graph


def compile_graph():
    """Compile the graph. Call once at startup."""
    g = build_graph()
    return g.compile()


# Module-level compiled graph (import this in webhook/main)
compiled_graph = compile_graph()
