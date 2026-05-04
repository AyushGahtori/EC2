"""
fetch_pr.py - Node: fetch_pr_node

Fetches PR metadata, file list, and raw diffs from the GitHub API.
Also loads repo guidelines from CONTRIBUTING.md if available.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from pr_copilot.config import settings
from pr_copilot.state import PRCopilotState, PRFile

logger = logging.getLogger(__name__)

GITHUB_HEADERS = {
    "Authorization": f"Bearer {settings.github_token}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# Maximum diff characters we store per file (safety cap)
MAX_PATCH_CHARS = 30_000


def fetch_pr_node(state: PRCopilotState) -> PRCopilotState:
    """
    Fetches PR data from GitHub REST API.

    Populates:
        state.pr_title, state.pr_body
        state.base_sha, state.head_sha
        state.files   (list[PRFile])
        state.diffs   (dict filename -> patch)
        state.repo_guidelines
    """
    repo = state.repo
    pr_number = state.pr_number
    base_url = f"{settings.github_api_base}/repos/{repo}/pulls/{pr_number}"

    try:
        with httpx.Client(headers=GITHUB_HEADERS, timeout=30) as client:
            # ── 1. PR metadata ───────────────────────────────────────────────
            pr_resp = client.get(base_url)
            pr_resp.raise_for_status()
            pr_data = pr_resp.json()

            state = state.model_copy(update={
                "pr_title": pr_data.get("title", ""),
                "pr_body": pr_data.get("body", "") or "",
                "base_sha": pr_data["base"]["sha"],
                "head_sha": pr_data["head"]["sha"],
            })

            # ── 2. Changed files ──────────────────────────────────────────────
            files_url = f"{base_url}/files"
            files_resp = client.get(files_url, params={"per_page": 100})
            files_resp.raise_for_status()
            raw_files = files_resp.json()

            pr_files: list[PRFile] = []
            diffs: dict[str, str] = {}

            for f in raw_files:
                filename = f["filename"]
                patch = f.get("patch", "") or ""
                # Truncate oversized patches
                if len(patch) > MAX_PATCH_CHARS:
                    patch = patch[:MAX_PATCH_CHARS] + "\n... [truncated]"

                pr_files.append(
                    PRFile(
                        filename=filename,
                        status=f.get("status", "modified"),
                        additions=f.get("additions", 0),
                        deletions=f.get("deletions", 0),
                        patch=patch,
                        sha=f.get("sha", ""),
                        blob_url=f.get("blob_url", ""),
                    )
                )
                if patch:
                    diffs[filename] = patch

            state = state.model_copy(update={"files": pr_files, "diffs": diffs})
            logger.info(
                "Fetched PR #%s (%s): %d files changed",
                pr_number, repo, len(pr_files),
            )

    except httpx.HTTPStatusError as exc:
        logger.error("GitHub API error: %s", exc.response.text)
        state = state.model_copy(update={"error": f"GitHub API error: {exc}"})
    except Exception as exc:
        logger.exception("Unexpected error in fetch_pr_node")
        state = state.model_copy(update={"error": str(exc)})

    # ── 3. Repo guidelines ────────────────────────────────────────────────────
    guidelines_path = Path(settings.guidelines_file)
    if guidelines_path.exists():
        state = state.model_copy(
            update={"repo_guidelines": guidelines_path.read_text(encoding="utf-8")}
        )
    else:
        state = state.model_copy(
            update={"repo_guidelines": "Follow PEP-8, write docstrings, avoid bare excepts."}
        )

    return state
