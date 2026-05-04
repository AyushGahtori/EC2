from __future__ import annotations

import hashlib
import hmac
import os
import sys
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
try:
    from pydantic import ConfigDict
except Exception:  # pragma: no cover
    ConfigDict = None  # type: ignore

ROOT = Path(__file__).resolve().parent
EC2_ROOT = ROOT.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(EC2_ROOT))

from ec2_shared.agent_response import as_text, card, failed, needs_input, require_fields, success
from ec2_shared.ui import render_agent_window

AGENT_ID = "pr-copilot-review-agent"
AGENT_NAME = "PR Copilot Review Agent"


class ActionRequest(BaseModel):
    action: str | None = None
    if ConfigDict:
        model_config = ConfigDict(extra="allow")
    else:
        class Config:
            extra = "allow"


def _payload(model: ActionRequest) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, dict):
        return value
    return {}


def _verify_signature(payload: bytes, signature_header: str) -> bool:
    secret = os.getenv("WEBHOOK_SECRET", "")
    if not secret:
        return True
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header or "")


def _run_review_sync(repo: str, pr_number: int, dry_run: bool) -> dict[str, Any]:
    from pr_copilot.graph import compiled_graph
    from pr_copilot.state import PRCopilotState

    initial_state = PRCopilotState(repo=repo, pr_number=pr_number, skipped=dry_run)
    result = compiled_graph.invoke(initial_state)
    final_state = result if isinstance(result, PRCopilotState) else PRCopilotState.model_validate(result)
    return _to_dict(final_state)


async def _run_review(payload: dict[str, Any], action: str) -> dict[str, Any]:
    repo = as_text(payload.get("repo") or payload.get("repository") or payload.get("repository_full_name"))
    pr_number_raw = payload.get("pr_number") or payload.get("pr") or payload.get("pull_request")
    missing = []
    if not repo:
        missing.append("repo")
    if pr_number_raw is None or as_text(pr_number_raw) == "":
        missing.append("pr_number")
    if missing:
        return needs_input(
            agent=AGENT_ID,
            action=action,
            message="PR Copilot needs a GitHub repository and pull request number.",
            missing_fields=missing,
        )

    pr_number = int(pr_number_raw)
    dry_run = bool(payload.get("dry_run", True))
    state = _run_review_sync(repo, pr_number, dry_run)
    comments = state.get("final_comments") or []
    bandit = state.get("bandit_results") or []
    flake8 = state.get("flake8_results") or []
    summary = as_text(state.get("review_summary")) or (
        f"PR review completed with {len(comments)} comments."
    )
    status = "partial_success" if state.get("error") else "success"
    response = success(
        agent=AGENT_ID,
        action=action,
        summary=summary,
        result={
            "repo": repo,
            "pr_number": pr_number,
            "dry_run": dry_run,
            "review_summary": summary,
            "has_issues": state.get("has_issues"),
            "comments": comments,
            "bandit_issues": bandit,
            "flake8_issues": flake8,
            "error": state.get("error"),
        },
        cards=[
            card("Review summary", summary, {"repo": repo, "pr_number": pr_number, "dry_run": dry_run}),
            card("Findings", "Static analysis and LLM review completed.", {"comments": len(comments), "bandit": len(bandit), "flake8": len(flake8)}),
            *[
                card(
                    f"{as_text(item.get('severity')).upper() or 'ISSUE'}: {as_text(item.get('category'))}",
                    as_text(item.get("message")),
                    {"file": item.get("filename"), "line": item.get("line"), "suggestion": item.get("suggestion")},
                )
                for item in comments[:5]
                if isinstance(item, dict)
            ],
        ],
        logs=["Fetched PR files, ran Bandit and Flake8, chunked diffs, reviewed with the configured LLM, and validated comments."],
        next_actions=["Inspect inline comments", "Run with dry_run=false to post comments", "Review webhook delivery logs"],
        internal=state,
    )
    response["status"] = status
    return response


async def _webhook_status(action: str) -> dict[str, Any]:
    configured = bool(os.getenv("WEBHOOK_SECRET"))
    token = bool(os.getenv("GITHUB_TOKEN"))
    return success(
        agent=AGENT_ID,
        action=action,
        summary="PR Copilot webhook status loaded.",
        result={"webhook_secret_configured": configured, "github_token_configured": token},
        cards=[card("Webhook status", "Webhook checks are ready when GitHub token and optional secret are configured.", {"GITHUB_TOKEN": token, "WEBHOOK_SECRET": configured})],
    )


async def _background_review(repo: str, pr_number: int) -> None:
    try:
        _run_review_sync(repo, pr_number, dry_run=False)
    except Exception:
        pass


CAPABILITIES = [
    {"name": "review_pr", "label": "Review PR", "description": "Fetch a GitHub PR, run static analysis, run LLM review, validate, and optionally post comments.", "required": ["repo", "pr_number"], "optional": ["dry_run"]},
    {"name": "dry_run_review", "label": "Dry Run Review", "description": "Run the full pipeline without posting comments.", "required": ["repo", "pr_number"], "optional": []},
    {"name": "webhook_status", "label": "Webhook Status", "description": "Check token/secret readiness for GitHub PR webhooks.", "required": [], "optional": []},
    {"name": "list_capabilities", "label": "List Capabilities", "description": "Show PR Copilot capabilities.", "required": [], "optional": []},
]

UI_SPEC = {
    "name": AGENT_NAME,
    "description": "AI pull-request reviewer that fetches PR diffs, runs Bandit and Flake8, chunks large diffs, uses an LLM with retry validation, and posts comments when approved.",
    "endpoint": "/pr-copilot/action",
    "actions": CAPABILITIES,
    "examples": [
        "Review AyushGahtori/SaaS-ai PR 42 as a dry run.",
        "Run PR Copilot on owner/repo pull request 17 and show the comments only.",
        "Check whether the PR Copilot webhook is configured.",
    ],
    "scope": [
        "Python PR review, static analysis, chunked diff review, GitHub webhook, and comment posting.",
        "Requires GITHUB_TOKEN for real GitHub reads and writes.",
    ],
    "usage": [
        "Use dry_run_review during normal chat unless you explicitly want comments posted.",
        "Use webhook_status before connecting GitHub webhooks.",
        "Set WEBHOOK_SECRET to validate inbound GitHub signatures.",
    ],
}

app = FastAPI(title=AGENT_NAME, version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/", response_class=HTMLResponse)
async def root() -> str:
    return render_agent_window(UI_SPEC)


@app.get("/pr-copilot/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "agent": AGENT_ID, "github_token_configured": bool(os.getenv("GITHUB_TOKEN"))}


@app.get("/pr-copilot/webhook/status")
async def webhook_status() -> dict[str, Any]:
    return await _webhook_status("webhook_status")


@app.post("/pr-copilot/action")
async def action(request: ActionRequest) -> dict[str, Any]:
    payload = _payload(request)
    selected = as_text(payload.get("action") or "dry_run_review")
    try:
        if selected in {"review_pr", "dry_run_review"}:
            if selected == "dry_run_review":
                payload["dry_run"] = True
            return await _run_review(payload, selected)
        if selected == "webhook_status":
            return await _webhook_status(selected)
        if selected == "list_capabilities":
            return success(agent=AGENT_ID, action=selected, summary="PR Copilot capabilities loaded.", result={"actions": CAPABILITIES}, cards=[card("Capabilities", "PR Copilot can review PRs, dry-run findings, and receive GitHub webhooks.", {"actions": ", ".join(item["name"] for item in CAPABILITIES)})])
        return needs_input(agent=AGENT_ID, action=selected, message=f"PR Copilot does not expose the action '{selected}'.", missing_fields=["action"])
    except Exception as exc:
        return failed(agent=AGENT_ID, action=selected, public_message="PR Copilot could not complete this request yet.", error=exc)


@app.post("/pr-copilot/webhook/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str = Header(default=""),
) -> dict[str, Any]:
    body = await request.body()
    if not _verify_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    payload = await request.json()
    if x_github_event != "pull_request":
        return {"status": "ignored", "event": x_github_event}
    event_action = payload.get("action", "")
    if event_action not in {"opened", "synchronize", "reopened"}:
        return {"status": "ignored", "action": event_action}
    repo = as_text((payload.get("repository") or {}).get("full_name"))
    pr_number = (payload.get("pull_request") or {}).get("number")
    if not repo or not pr_number:
        raise HTTPException(status_code=400, detail="Missing repo or PR number")
    background_tasks.add_task(_background_review, repo, int(pr_number))
    return {"status": "accepted", "repo": repo, "pr_number": int(pr_number), "action": event_action}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8053)
