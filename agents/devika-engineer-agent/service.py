from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from schemas import DevikaEngineerActionRequest, DevikaEngineerActionResponse
from store import (
    get_cached_result,
    get_status_summary,
    list_recent_snapshots,
    save_cached_result,
    save_snapshot,
)

try:
    import tiktoken
except Exception:  # pragma: no cover - optional dependency
    tiktoken = None


DEFAULT_MODEL = (
    os.getenv("DEVIKA_ENGINEER_MODEL")
    or os.getenv("GEMINI_MODEL_FLASH")
    or os.getenv("GEMINI_MODEL")
    or os.getenv("GEMINI_MODEL_PRO")
    or "gemini-2.5-flash"
).strip()

CACHE_TTL_SECONDS = int((os.getenv("DEVIKA_CACHE_TTL_SECONDS") or "900").strip() or "900")
HOT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

SUPPORTED_ACTIONS = {
    "run_devika_agent",
    "plan_project",
    "research_plan",
    "implement_feature",
    "fix_bug",
    "run_project",
    "deploy_project",
    "generate_report",
    "answer_question",
    "repo_intake",
    "browser_strategy",
    "list_snapshots",
    "agent_status",
    "token_estimate",
}


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _truncate(value: str, max_chars: int = 1200) -> str:
    raw = value.strip()
    if len(raw) <= max_chars:
        return raw
    return f"{raw[:max_chars]}..."


def _to_lines(value: str, limit: int = 6) -> list[str]:
    chunks = [part.strip("- ").strip() for part in re.split(r"[\n\r•]+", value) if part and part.strip()]
    deduped: list[str] = []
    seen: set[str] = set()
    for item in chunks:
        norm = item.lower()
        if norm in seen:
            continue
        seen.add(norm)
        deduped.append(item)
        if len(deduped) >= limit:
            break
    return deduped


def _extract_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    for pattern in (r"```json\s*([\s\S]*?)\s*```", r"```\s*([\s\S]*?)\s*```", r"(\{[\s\S]*\})"):
        match = re.search(pattern, text)
        if not match:
            continue
        snippet = match.group(1)
        try:
            parsed = json.loads(snippet)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return {}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _primary_prompt(req: DevikaEngineerActionRequest) -> str:
    candidates = [
        req.prompt,
        req.objective,
        req.featureRequest,
        req.question,
        req.errorLog,
        req.stackTrace,
        req.codeSnippet,
        req.codebaseSummary,
    ]
    for item in candidates:
        cleaned = _clean(item)
        if cleaned:
            return cleaned
    return ""


def _normalize_action(action: str) -> str:
    raw = _clean(action).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "run": "run_devika_agent",
        "start": "run_devika_agent",
        "plan": "plan_project",
        "research": "research_plan",
        "feature": "implement_feature",
        "bug": "fix_bug",
        "debug": "fix_bug",
        "deploy": "deploy_project",
        "report": "generate_report",
        "answer": "answer_question",
        "repo": "repo_intake",
        "browser": "browser_strategy",
        "snapshots": "list_snapshots",
        "status": "agent_status",
        "tokens": "token_estimate",
    }
    return aliases.get(raw, raw)


def _infer_action_from_prompt(prompt: str) -> str:
    p = prompt.lower()
    if any(token in p for token in ["stack trace", "error", "bug", "fails", "exception"]):
        return "fix_bug"
    if any(token in p for token in ["new feature", "add feature", "implement", "extend"]):
        return "implement_feature"
    if any(token in p for token in ["deploy", "release", "production rollout"]):
        return "deploy_project"
    if any(token in p for token in ["report", "documentation", "summary doc"]):
        return "generate_report"
    if any(token in p for token in ["research", "look up", "find info", "web search"]):
        return "research_plan"
    if any(token in p for token in ["run project", "start server", "execute"]):
        return "run_project"
    if any(token in p for token in ["clone", "repository", "github.com", "gitlab.com"]):
        return "repo_intake"
    if any(token in p for token in ["browser", "click", "form", "website flow"]):
        return "browser_strategy"
    if any(token in p for token in ["question", "explain", "what does", "why does"]):
        return "answer_question"
    return "plan_project"


def _require_user(req: DevikaEngineerActionRequest) -> str:
    user_id = _clean(req.userId)
    if not user_id:
        raise ValueError("userId is required for devika-engineer-agent actions.")
    return user_id


def _token_estimate(text: str) -> int:
    if not text:
        return 0
    if tiktoken is None:
        return max(1, len(text.split()))
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text.split()))


def _make_cache_key(req: DevikaEngineerActionRequest, action: str) -> str:
    payload = {
        "action": action,
        "chatId": _clean(req.chatId),
        "sessionId": _clean(req.sessionId),
        "prompt": _primary_prompt(req),
        "objective": _clean(req.objective),
        "featureRequest": _clean(req.featureRequest),
        "question": _clean(req.question),
        "errorLog": _truncate(_clean(req.errorLog), 800),
        "stackTrace": _truncate(_clean(req.stackTrace), 800),
        "codeSnippet": _truncate(_clean(req.codeSnippet), 800),
        "projectName": _clean(req.projectName),
        "repositoryUrl": _clean(req.repositoryUrl),
        "branch": _clean(req.branch),
        "files": sorted([_clean(item) for item in req.files if _clean(item)]),
        "constraints": sorted([_clean(item) for item in req.constraints if _clean(item)]),
        "context": req.context or {},
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _read_hot_cache(cache_key: str) -> tuple[dict[str, Any] | None, str | None]:
    now = time.time()
    item = HOT_CACHE.get(cache_key)
    if not item:
        return None, None
    expires_at, payload = item
    if now >= expires_at:
        HOT_CACHE.pop(cache_key, None)
        return None, None
    return payload, "memory"


def _write_hot_cache(cache_key: str, payload: dict[str, Any]) -> None:
    HOT_CACHE[cache_key] = (time.time() + CACHE_TTL_SECONDS, payload)


async def _gemini_json(system_prompt: str, user_payload: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        return fallback

    request_payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps(user_payload, ensure_ascii=True)}]}],
        "generationConfig": {
            "temperature": 0.25,
            "topP": 0.9,
            "responseMimeType": "application/json",
            "maxOutputTokens": 2000,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=35.0) as client:
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{DEFAULT_MODEL}:generateContent",
                params={"key": api_key},
                json=request_payload,
            )
            response.raise_for_status()
            body = response.json()
        parts = body.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        text = "\n".join(str(part.get("text", "")) for part in parts if isinstance(part, dict)).strip()
        parsed = _extract_json(text)
        return parsed or fallback
    except Exception:
        return fallback


def _ensure_prompt_for(action: str, req: DevikaEngineerActionRequest) -> str | None:
    prompt = _primary_prompt(req)
    if prompt:
        return prompt

    if action in {"list_snapshots", "agent_status"}:
        return ""
    if action == "repo_intake":
        if _clean(req.repositoryUrl):
            return req.repositoryUrl or ""
        return None
    if action == "token_estimate":
        return ""
    return None


def _safe_repo_metadata(raw_url: str) -> dict[str, Any] | None:
    url = _clean(raw_url)
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None

    if parsed.scheme not in {"http", "https", "ssh"} and not url.startswith("git@"):
        return None

    if url.startswith("git@"):
        # git@github.com:owner/repo.git
        chunks = url.split(":", 1)
        host = chunks[0].split("@", 1)[-1]
        path = chunks[1] if len(chunks) > 1 else ""
    else:
        host = parsed.netloc
        path = parsed.path.lstrip("/")

    path = path.removesuffix(".git").strip("/")
    if host.lower() not in {"github.com", "gitlab.com", "bitbucket.org"}:
        return None
    if "/" not in path:
        return None

    owner, repo = path.split("/", 1)
    if not owner or not repo:
        return None

    return {
        "host": host.lower(),
        "owner": owner,
        "repo": repo,
        "canonicalUrl": f"https://{host.lower()}/{owner}/{repo}",
        "cloneCommand": f"git clone https://{host.lower()}/{owner}/{repo}.git",
    }


def _base_response(result_type: str, message: str, summary: str, payload: dict[str, Any]) -> DevikaEngineerActionResponse:
    return DevikaEngineerActionResponse(
        status="success",
        type=result_type,
        message=message,
        summary=summary,
        result=payload,
    )


async def _plan_project(req: DevikaEngineerActionRequest, prompt: str) -> DevikaEngineerActionResponse:
    fallback = {
        "projectName": _clean(req.projectName) or "Untitled Project",
        "focus": "Convert the objective into a safe, testable implementation plan.",
        "plan": [
            {"step": 1, "title": "Clarify scope", "details": "Confirm expected inputs, outputs, and constraints."},
            {"step": 2, "title": "Design approach", "details": "Outline architecture and data contracts before coding."},
            {"step": 3, "title": "Implement incrementally", "details": "Ship smallest working slice first, then expand."},
            {"step": 4, "title": "Verify behavior", "details": "Run happy-path and failure-path validation."},
            {"step": 5, "title": "Document + handoff", "details": "Capture actions, tests, and operational notes."},
        ],
        "risks": [
            "Ambiguous requirements can trigger rework.",
            "Missing environment configuration can block runtime checks.",
            "Large unscoped prompts can cause broad low-quality output.",
        ],
        "acceptanceCriteria": [
            "Feature works for at least one realistic user scenario.",
            "Failure path returns actionable next step.",
            "Result is documented and reproducible.",
        ],
        "estimatedComplexity": "medium",
    }

    structured = await _gemini_json(
        "You are a senior software planning agent. Return strict JSON with keys "
        "projectName, focus, plan(array of step/title/details), risks(array), "
        "acceptanceCriteria(array), estimatedComplexity.",
        {
            "prompt": prompt,
            "projectName": req.projectName,
            "constraints": req.constraints,
            "files": req.files,
            "context": req.context,
        },
        fallback,
    )

    return _base_response(
        "devika_plan_result",
        "Project execution plan prepared.",
        "Generated a phased engineering plan with risks and acceptance criteria.",
        structured,
    )


def _derive_queries(prompt: str, limit: int = 6) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9_]{4,}", prompt.lower())
    stop = {"that", "this", "with", "from", "have", "your", "will", "into", "about", "where", "when", "what"}
    ranked: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in stop:
            continue
        if token in seen:
            continue
        seen.add(token)
        ranked.append(token)
        if len(ranked) >= limit:
            break
    if not ranked:
        return [prompt[:80]]
    return [f"{item} best practices" for item in ranked[:3]] + [f"{item} implementation examples" for item in ranked[3:]]


async def _duckduckgo_findings(queries: list[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=12.0) as client:
        for query in queries[:4]:
            try:
                response = await client.get(
                    "https://api.duckduckgo.com/",
                    params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
                )
                response.raise_for_status()
                data = response.json()
                summary = _clean(data.get("AbstractText") or "")
                source = _clean(data.get("AbstractURL") or "")
                if summary:
                    findings.append({"query": query, "source": source or "DuckDuckGo Instant Answer", "snippet": summary})
            except Exception:
                continue
    return findings


async def _research_plan(req: DevikaEngineerActionRequest, prompt: str) -> DevikaEngineerActionResponse:
    queries = _derive_queries(prompt)
    findings = await _duckduckgo_findings(queries)
    fallback = {
        "queries": queries,
        "findings": findings,
        "openQuestions": [
            "Which constraints are strict vs flexible?",
            "What input and output examples should drive implementation?",
            "Which failure mode is most expensive in production?",
        ],
        "nextActions": [
            "Confirm one concrete success metric.",
            "Lock dependencies and API contracts.",
            "Proceed with implementation plan using validated assumptions.",
        ],
    }

    structured = await _gemini_json(
        "You are a software research planner. Return strict JSON with keys queries, findings, "
        "openQuestions, nextActions. Keep findings practical for implementation.",
        {
            "prompt": prompt,
            "derivedQueries": queries,
            "initialFindings": findings,
            "context": req.context,
        },
        fallback,
    )

    return _base_response(
        "devika_research_result",
        "Research strategy prepared.",
        "Prepared targeted research queries and implementation-oriented findings.",
        structured,
    )


async def _implement_feature(req: DevikaEngineerActionRequest, prompt: str) -> DevikaEngineerActionResponse:
    fallback = {
        "implementationPlan": [
            "Map requested behavior to explicit inputs/outputs.",
            "Add or update data schema contracts.",
            "Implement feature logic in isolated units.",
            "Add integration glue and guardrails for invalid inputs.",
        ],
        "suggestedFiles": [
            {"path": "service.py", "changeType": "update", "rationale": "Feature core logic and validations."},
            {"path": "schemas.py", "changeType": "update", "rationale": "Request/response contract updates."},
            {"path": "server.py", "changeType": "update", "rationale": "Route wiring and health-safe responses."},
        ],
        "testPlan": [
            "Happy path with complete inputs.",
            "Missing required input returns needs_input.",
            "Unknown action returns failed with actionable error.",
        ],
        "rollbackPlan": ["Feature flag or action alias removal", "Revert schema additions that break compatibility"],
    }

    structured = await _gemini_json(
        "You are a feature implementation architect. Return strict JSON with keys implementationPlan, "
        "suggestedFiles(path/changeType/rationale), testPlan, rollbackPlan.",
        {
            "featureRequest": _clean(req.featureRequest) or prompt,
            "projectName": req.projectName,
            "files": req.files,
            "constraints": req.constraints,
            "codebaseSummary": req.codebaseSummary,
            "context": req.context,
        },
        fallback,
    )

    return _base_response(
        "devika_feature_result",
        "Feature implementation guide generated.",
        "Prepared step-by-step feature plan with tests and rollback strategy.",
        structured,
    )


async def _fix_bug(req: DevikaEngineerActionRequest, prompt: str) -> DevikaEngineerActionResponse:
    error_context = _clean(req.errorLog) or _clean(req.stackTrace) or prompt
    fallback = {
        "likelyRootCauses": _to_lines(error_context, limit=4)
        or [
            "Input validation gap allows invalid state.",
            "Assumption mismatch between schema and runtime payload.",
            "Unhandled edge path bypasses expected return contract.",
        ],
        "debugChecklist": [
            "Reproduce with exact payload and environment variables.",
            "Check action normalization and parameter extraction.",
            "Verify response contract fields: status, summary, error.",
            "Add regression guard for reproduced failure.",
        ],
        "proposedFix": [
            "Add strict required-field validation and readable needs_input response.",
            "Normalize action aliases before execution dispatch.",
            "Wrap external calls with timeout and fallback path.",
        ],
        "verificationSteps": [
            "Happy path returns success.",
            "Malformed payload returns needs_input.",
            "Unsupported action returns failed with clear error text.",
        ],
    }

    structured = await _gemini_json(
        "You are a debugging specialist. Return strict JSON with keys likelyRootCauses, debugChecklist, "
        "proposedFix, verificationSteps.",
        {
            "prompt": prompt,
            "errorLog": req.errorLog,
            "stackTrace": req.stackTrace,
            "codeSnippet": req.codeSnippet,
            "context": req.context,
        },
        fallback,
    )

    return _base_response(
        "devika_bugfix_result",
        "Bug triage and fix strategy prepared.",
        "Generated probable root causes with verification-focused patch guidance.",
        structured,
    )


async def _run_project(req: DevikaEngineerActionRequest, prompt: str) -> DevikaEngineerActionResponse:
    command = _clean(str(req.context.get("command") if isinstance(req.context, dict) else "")) or "npm run dev"
    payload = {
        "executionStrategy": {
            "recommendedCommand": command,
            "workingDirectory": _clean(req.projectName) or ".",
            "timeoutSeconds": 120,
        },
        "preflightChecks": [
            "Verify environment variables and secrets are injected.",
            "Install dependencies with lockfile-consistent package manager.",
            "Run lint/type checks before starting runtime.",
        ],
        "rerunPolicy": [
            "If startup fails, capture the first concrete error line.",
            "Classify failure as config/dependency/runtime and patch incrementally.",
            "Re-run from clean shell after each fix.",
        ],
        "notes": _to_lines(prompt, limit=3),
    }
    return _base_response(
        "devika_run_result",
        "Run strategy generated.",
        "Prepared a deterministic run and rerun strategy with preflight checks.",
        payload,
    )


async def _deploy_project(req: DevikaEngineerActionRequest, prompt: str) -> DevikaEngineerActionResponse:
    payload = {
        "deploymentChecklist": [
            "Confirm build command and output artifact path.",
            "Inject runtime secrets through environment or secret manager.",
            "Run smoke test on health route and one primary action route.",
            "Enable logs, uptime checks, and rollback target version.",
        ],
        "releasePlan": [
            "Deploy to staging first and validate action contract responses.",
            "Promote to production after one happy path and one failure path test.",
            "Track first-hour error budget and latency regression.",
        ],
        "riskControls": [
            "Do not expose raw stack traces in user-facing output.",
            "Pin dependencies to reduce drift between environments.",
            "Keep cached snapshots bounded with TTL and eviction.",
        ],
        "promptContext": _to_lines(prompt, limit=4),
    }
    return _base_response(
        "devika_deploy_result",
        "Deployment plan prepared.",
        "Generated deployment and rollback checklist aligned to production safety.",
        payload,
    )


async def _generate_report(req: DevikaEngineerActionRequest, prompt: str) -> DevikaEngineerActionResponse:
    fallback_summary = (
        "## Engineering Report\n"
        f"- Objective: {_truncate(prompt, 220)}\n"
        "- Scope: Planning, implementation, validation, and deployment readiness.\n"
        "- Risks: Ambiguous requirements, environment drift, and missing test coverage.\n"
        "- Recommended next step: run one complete happy-path + one failure-path smoke test."
    )

    report = await _gemini_json(
        "You are a technical report generator. Return strict JSON with keys title, executiveSummary, "
        "highlights(array), risks(array), nextSteps(array), markdown.",
        {
            "prompt": prompt,
            "projectName": req.projectName,
            "files": req.files,
            "context": req.context,
        },
        {
            "title": "Devika Engineering Report",
            "executiveSummary": "Compiled a concise engineering summary.",
            "highlights": ["Action plan prepared", "Validation matrix drafted", "Operational risks identified"],
            "risks": ["Environment mismatch", "Insufficient negative-path tests"],
            "nextSteps": ["Run type checks", "Run smoke tests", "Document rollout state"],
            "markdown": fallback_summary,
        },
    )

    return _base_response(
        "devika_report_result",
        "Project report generated.",
        "Generated an engineering report with risks and next steps.",
        report,
    )


async def _answer_question(req: DevikaEngineerActionRequest, prompt: str) -> DevikaEngineerActionResponse:
    fallback = {
        "answer": _truncate(prompt, 240),
        "reasoning": "Answered using available agent context and software engineering defaults.",
        "followups": [
            "Share concrete files or stack traces for a more exact answer.",
            "Specify whether you need architecture-level or code-level guidance.",
        ],
    }
    structured = await _gemini_json(
        "You are a senior software engineer answering implementation questions. Return strict JSON "
        "with keys answer, reasoning, followups(array).",
        {"question": _clean(req.question) or prompt, "context": req.context, "codebaseSummary": req.codebaseSummary},
        fallback,
    )

    return _base_response(
        "devika_answer_result",
        "Technical answer prepared.",
        "Provided a direct engineering answer with suggested follow-ups.",
        structured,
    )


async def _repo_intake(req: DevikaEngineerActionRequest, prompt: str) -> DevikaEngineerActionResponse:
    source = _clean(req.repositoryUrl) or _clean(prompt)
    metadata = _safe_repo_metadata(source)
    if metadata is None:
        return DevikaEngineerActionResponse(
            status="needs_input",
            type="devika_repo_result",
            message="Repository URL is missing or invalid.",
            summary="Please provide a valid GitHub/GitLab/Bitbucket repository URL.",
            result={"suggestedInputs": ["repositoryUrl"]},
            error="invalid_repository_url",
        )

    payload = {
        **metadata,
        "branch": _clean(req.branch) or "main",
        "intakeChecklist": [
            "Clone repository and install dependencies.",
            "Run lint and type checks to establish baseline.",
            "Map high-risk modules and integration boundaries.",
            "Prepare first small change with tests before large refactors.",
        ],
    }
    return _base_response(
        "devika_repo_result",
        "Repository intake prepared.",
        f"Prepared onboarding checklist for {metadata['owner']}/{metadata['repo']}.",
        payload,
    )


async def _browser_strategy(req: DevikaEngineerActionRequest, prompt: str) -> DevikaEngineerActionResponse:
    steps = [
        "Open target website and verify authenticated session state.",
        "Navigate to the exact screen tied to the user objective.",
        "Perform one action at a time while capturing visible UI feedback.",
        "If blocked by confirmation dialog, pause and verify side effects before continuing.",
        "Capture final state and summarize changes done on the website.",
    ]
    payload = {
        "objective": _truncate(prompt, 240),
        "interactionPlan": steps,
        "safetyNotes": [
            "Avoid destructive clicks without explicit user intent.",
            "Never expose secret tokens in browser logs or screenshots.",
            "Confirm irreversible actions before final submit step.",
        ],
    }
    return _base_response(
        "devika_browser_result",
        "Browser interaction strategy generated.",
        "Prepared an interaction playbook with operational safety guardrails.",
        payload,
    )


async def _list_snapshots(user_id: str) -> DevikaEngineerActionResponse:
    snapshots = list_recent_snapshots(user_id, limit=12)
    trimmed = []
    for item in snapshots:
        trimmed.append(
            {
                "snapshotId": item.get("snapshotId"),
                "action": item.get("action"),
                "status": item.get("status"),
                "createdAtIso": item.get("createdAtIso"),
            }
        )
    return _base_response(
        "devika_snapshots_result",
        f"Loaded {len(trimmed)} recent snapshots.",
        "Recent Devika execution snapshots loaded.",
        {"snapshots": trimmed},
    )


async def _agent_status(user_id: str) -> DevikaEngineerActionResponse:
    summary = get_status_summary(user_id, days=7)
    return _base_response(
        "devika_status_result",
        "Agent status summary generated.",
        "Last seven days execution summary prepared.",
        summary,
    )


async def _token_estimate_action(prompt: str) -> DevikaEngineerActionResponse:
    count = _token_estimate(prompt)
    return _base_response(
        "devika_token_result",
        "Token estimate generated.",
        f"Estimated token count: {count}",
        {"tokenEstimate": count, "modelFamily": "cl100k_base"},
    )


async def _dispatch(
    req: DevikaEngineerActionRequest,
    action: str,
    prompt: str,
    user_id: str,
) -> DevikaEngineerActionResponse:
    if action == "plan_project":
        return await _plan_project(req, prompt)
    if action == "research_plan":
        return await _research_plan(req, prompt)
    if action == "implement_feature":
        return await _implement_feature(req, prompt)
    if action == "fix_bug":
        return await _fix_bug(req, prompt)
    if action == "run_project":
        return await _run_project(req, prompt)
    if action == "deploy_project":
        return await _deploy_project(req, prompt)
    if action == "generate_report":
        return await _generate_report(req, prompt)
    if action == "answer_question":
        return await _answer_question(req, prompt)
    if action == "repo_intake":
        return await _repo_intake(req, prompt)
    if action == "browser_strategy":
        return await _browser_strategy(req, prompt)
    if action == "list_snapshots":
        return await _list_snapshots(user_id)
    if action == "agent_status":
        return await _agent_status(user_id)
    if action == "token_estimate":
        return await _token_estimate_action(prompt)

    return DevikaEngineerActionResponse(
        status="failed",
        type="devika_status_result",
        message=f"Unsupported action: {req.action}",
        summary="Requested action is not supported by devika-engineer-agent.",
        error=f"unknown_action:{req.action}",
    )


def _attach_cache_meta(resp: DevikaEngineerActionResponse, hit: bool, source: str, cache_key: str) -> DevikaEngineerActionResponse:
    payload = dict(resp.result or {})
    payload["cache"] = {"hit": hit, "source": source, "cacheKey": cache_key, "ttlSeconds": CACHE_TTL_SECONDS}
    resp.result = payload
    return resp


async def run_devika_engineer_action(
    req: DevikaEngineerActionRequest,
) -> DevikaEngineerActionResponse:
    try:
        user_id = _require_user(req)
        normalized_action = _normalize_action(req.action)

        if normalized_action not in SUPPORTED_ACTIONS:
            return DevikaEngineerActionResponse(
                status="failed",
                type="devika_status_result",
                message=f"Unsupported action: {req.action}",
                summary="Please use one of the supported devika-engineer-agent actions.",
                result={"supportedActions": sorted(SUPPORTED_ACTIONS)},
                error=f"unknown_action:{req.action}",
            )

        prompt = _ensure_prompt_for(normalized_action, req)
        if prompt is None:
            return DevikaEngineerActionResponse(
                status="needs_input",
                type="devika_status_result",
                message="I need more detail to continue.",
                summary="Please provide a prompt/objective or the missing required field for this action.",
                result={"suggestedInputs": ["prompt"]},
                error="missing_prompt",
            )

        effective_action = normalized_action
        if normalized_action == "run_devika_agent":
            inferred = _infer_action_from_prompt(prompt)
            effective_action = inferred

        cacheable = effective_action not in {"list_snapshots", "agent_status", "token_estimate"}
        cache_key = _make_cache_key(req, effective_action)

        if cacheable and not req.forceRefresh:
            cached_payload, source = _read_hot_cache(cache_key)
            if cached_payload and source:
                parsed = DevikaEngineerActionResponse(**cached_payload)
                return _attach_cache_meta(parsed, True, source, cache_key)

            cached_payload, source = get_cached_result(user_id, cache_key, CACHE_TTL_SECONDS)
            if cached_payload and source:
                _write_hot_cache(cache_key, cached_payload)
                parsed = DevikaEngineerActionResponse(**cached_payload)
                return _attach_cache_meta(parsed, True, source, cache_key)

        response = await _dispatch(req, effective_action, prompt, user_id)
        if response.status in {"success", "partial_success"}:
            response = _attach_cache_meta(response, False, "miss", cache_key)

        if cacheable and response.status in {"success", "partial_success"}:
            body = response.model_dump()
            _write_hot_cache(cache_key, body)
            save_cached_result(user_id, cache_key, body)

        snapshot_id = save_snapshot(
            user_id=user_id,
            action=effective_action,
            payload=response.model_dump(),
            status=response.status,
            cache_key=cache_key if cacheable else None,
        )
        payload = dict(response.result or {})
        payload["snapshotId"] = snapshot_id
        payload["executedAction"] = effective_action
        response.result = payload

        return response
    except ValueError as exc:
        return DevikaEngineerActionResponse(
            status="needs_input",
            type="devika_status_result",
            message=str(exc),
            summary="Required fields are missing for this request.",
            result={"suggestedInputs": ["userId"]},
            error=str(exc),
        )
    except Exception as exc:
        return DevikaEngineerActionResponse(
            status="failed",
            type="devika_status_result",
            message="Devika Engineer Agent failed to process the request.",
            summary="Retry with a clearer prompt, then provide code/error context if it still fails.",
            error=f"devika_engineer_failed:{exc}",
        )
