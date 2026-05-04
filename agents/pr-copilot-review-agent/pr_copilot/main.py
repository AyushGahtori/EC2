"""
main.py - CLI entrypoint for manual PR review triggering.

Usage:
    python -m pr_copilot.main --repo owner/repo --pr 42
    python -m pr_copilot.main --repo owner/repo --pr 42 --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from pr_copilot.graph import compiled_graph
from pr_copilot.state import PRCopilotState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PR Copilot — AI-powered Pull Request reviewer"
    )
    parser.add_argument("--repo", required=True, help="GitHub repo in owner/repo format")
    parser.add_argument("--pr", type=int, required=True, help="PR number")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the full pipeline but skip posting comments to GitHub",
    )
    parser.add_argument(
        "--output-json",
        metavar="FILE",
        help="Write final state JSON to this file",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    initial_state = PRCopilotState(
        repo=args.repo,
        pr_number=args.pr,
        # In dry-run mode we mark skipped=True so post_comments_node is a no-op
        skipped=args.dry_run,
    )

    logger.info("Running PR Copilot on %s PR #%d (dry_run=%s)", args.repo, args.pr, args.dry_run)

    try:
        result = compiled_graph.invoke(initial_state)
        final_state = (
            result
            if isinstance(result, PRCopilotState)
            else PRCopilotState.model_validate(result)
        )
    except Exception as exc:
        logger.exception("Pipeline crashed: %s", exc)
        sys.exit(1)

    # ── Summary output ────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"  PR Copilot Review — {args.repo} PR #{args.pr}")
    print("=" * 60)
    print(f"  {final_state.review_summary}")
    print(f"  Files changed : {len(final_state.files)}")
    print(f"  Bandit issues : {len(final_state.bandit_results)}")
    print(f"  Flake8 issues : {len(final_state.flake8_results)}")
    print(f"  Total comments: {len(final_state.final_comments)}")
    if final_state.error:
        print(f"\n  ⚠️  Error: {final_state.error}")
    print("=" * 60 + "\n")

    for i, c in enumerate(final_state.final_comments, 1):
        sev = c.severity.upper()
        print(f"[{i:02d}] {sev:12} | {c.filename}:{c.line} | {c.category}")
        print(f"       {c.message}")
        if c.suggestion:
            print(f"       💡 Fix: {c.suggestion}")
        print()

    # ── Optional JSON output ──────────────────────────────────────────────────
    if args.output_json:
        out = {
            "repo": final_state.repo,
            "pr_number": final_state.pr_number,
            "review_summary": final_state.review_summary,
            "has_issues": final_state.has_issues,
            "comments": [c.model_dump() for c in final_state.final_comments],
            "bandit_issues": [b.model_dump() for b in final_state.bandit_results],
            "flake8_issues": [f.model_dump() for f in final_state.flake8_results],
            "error": final_state.error,
        }
        with open(args.output_json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
        logger.info("Wrote output to %s", args.output_json)

    sys.exit(1 if final_state.error else 0)


if __name__ == "__main__":
    main()
