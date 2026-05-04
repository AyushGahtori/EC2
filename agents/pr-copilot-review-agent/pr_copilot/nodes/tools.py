"""
tools.py - Nodes: run_bandit_node + run_flake8_node

Both nodes run static analysis tools on temporary copies of the PR diff.
They are designed to run in PARALLEL via LangGraph's fan-out.
"""

from __future__ import annotations
import sys
import json
import logging
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pr_copilot.config import settings
from pr_copilot.state import BanditIssue, Flake8Issue, PRCopilotState

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_temp_files(diffs: dict[str, str]) -> dict[str, Path]:
    """
    For each file in the diff, extract the added lines (+) and write
    them to a temp directory, preserving relative paths.
    Returns a mapping filename -> temp Path.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="pr_copilot_"))
    written: dict[str, Path] = {}

    for filename, patch in diffs.items():
        # Only analyse Python files
        if not filename.endswith(".py"):
            continue
        lines = []
        for line in patch.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                lines.append(line[1:])  # strip leading '+'
        if not lines:
            continue
        out_path = tmp_dir / filename
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines), encoding="utf-8")
        written[filename] = out_path

    return written


def _cleanup(paths: dict[str, Path]) -> None:
    import shutil
    if paths:
        try:
            root = next(iter(paths.values())).parent
            while root.parent != root:
                if root.name.startswith("pr_copilot_"):
                    shutil.rmtree(root, ignore_errors=True)
                    return
                root = root.parent
        except Exception:
            pass


# ── Bandit ────────────────────────────────────────────────────────────────────

def _run_bandit_on_file(filename: str, path: Path) -> list[BanditIssue]:
    """Run bandit on a single file, return parsed issues."""
    try:
        result = subprocess.run(
    [sys.executable, "-m", "bandit", "-f", "json", "-q", str(path)],
    capture_output=True,
    text=True,
    timeout=60,
)
        data = json.loads(result.stdout or "{}")
        issues: list[BanditIssue] = []
        for r in data.get("results", []):
            issues.append(
                BanditIssue(
                    filename=filename,
                    line=r.get("line_number", 0),
                    severity=r.get("issue_severity", "LOW"),
                    confidence=r.get("issue_confidence", "LOW"),
                    test_id=r.get("test_id", ""),
                    issue_text=r.get("issue_text", ""),
                    code=r.get("code", ""),
                )
            )
        return issues
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        logger.warning("Bandit failed for %s: %s", filename, exc)
        return []


def run_bandit_node(state: PRCopilotState) -> dict[str, object]:
    """LangGraph node: run Bandit security scan on all changed Python files."""
    if state.error:
        return {}

    temp_files = _write_temp_files(state.diffs)
    all_issues: list[BanditIssue] = []

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(_run_bandit_on_file, fname, path): fname
            for fname, path in temp_files.items()
        }
        for future in as_completed(futures):
            fname = futures[future]
            try:
                all_issues.extend(future.result())
            except Exception:
                logger.exception("Bandit worker crashed for %s", fname)

    _cleanup(temp_files)
    logger.info("Bandit: %d issues found", len(all_issues))
    return {"bandit_results": all_issues}


# ── Flake8 ────────────────────────────────────────────────────────────────────

def _run_flake8_on_file(filename: str, path: Path) -> list[Flake8Issue]:
    """Run flake8 on a single file, return parsed issues."""
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "flake8",
                "--format=%(row)d:%(col)d:%(code)s:%(text)s",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        issues: list[Flake8Issue] = []
        for raw_line in result.stdout.splitlines():
            parts = raw_line.split(":", 3)
            if len(parts) < 4:
                continue
            row, col, code, msg = parts
            try:
                row_i = int(row)
                col_i = int(col)
            except ValueError:
                logger.warning("Skipping unparsable flake8 output for %s: %r", filename, raw_line)
                continue
            issues.append(
                Flake8Issue(
                    filename=filename,
                    line=row_i,
                    col=col_i,
                    code=code.strip(),
                    message=msg.strip(),
                )
            )
        return issues
    except subprocess.TimeoutExpired as exc:
        logger.warning("Flake8 timed out for %s: %s", filename, exc)
        return []


def run_flake8_node(state: PRCopilotState) -> dict[str, object]:
    """LangGraph node: run flake8 style check on all changed Python files."""
    if state.error:
        return {}

    temp_files = _write_temp_files(state.diffs)
    all_issues: list[Flake8Issue] = []

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(_run_flake8_on_file, fname, path): fname
            for fname, path in temp_files.items()
        }
        for future in as_completed(futures):
            fname = futures[future]
            try:
                all_issues.extend(future.result())
            except Exception:
                logger.exception("Flake8 worker crashed for %s", fname)

    _cleanup(temp_files)
    logger.info("Flake8: %d issues found", len(all_issues))
    return {"flake8_results": all_issues}
