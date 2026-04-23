from __future__ import annotations

import copy
import os
from datetime import datetime
from typing import Any

from app.config import get_settings
from app.routes.analysis import agent as CYBER_AGENT
from app.services.windows_logs import MAX_LOG_LIMIT, fetch_event_logs
from app.utils import get_logger

from cache import TTLCache, make_cache_key
from schemas import CyberSocActionRequest, CyberSocActionResponse


logger = get_logger(__name__)
DISPLAY_NAME = "Cyber AI SOC Agent"

VALID_CHANNELS = {"Security", "System", "Application"}

ANALYZE_ACTIONS = {"analyze", "analyze_log", "analyze_security_log", "run_analysis"}
FETCH_WINDOWS_ACTIONS = {"fetch_windows_logs", "windows_logs", "get_windows_logs", "realtime_logs"}
ANALYZE_WINDOWS_ACTIONS = {"analyze_windows_logs", "analyze_realtime_logs"}
HISTORY_ACTIONS = {"history", "get_history", "list_history"}
CHANNEL_ACTIONS = {"list_windows_channels", "windows_channels", "list_channels"}
DASHBOARD_ACTIONS = {"dashboard", "dashboard_overview", "get_dashboard"}
CAPABILITY_ACTIONS = {"list_capabilities", "capabilities", "help", "actions"}

CACHE_MAX_ENTRIES = int((os.getenv("CYBER_SOC_CACHE_MAX_ENTRIES") or "512").strip() or "512")
ANALYZE_CACHE_TTL = int((os.getenv("CYBER_SOC_ANALYZE_CACHE_TTL_SECONDS") or "900").strip() or "900")
WINDOWS_CACHE_TTL = int((os.getenv("CYBER_SOC_WINDOWS_LOG_CACHE_TTL_SECONDS") or "30").strip() or "30")
DASHBOARD_CACHE_TTL = int((os.getenv("CYBER_SOC_DASHBOARD_CACHE_TTL_SECONDS") or "15").strip() or "15")

ANALYZE_CACHE = TTLCache(ttl_seconds=ANALYZE_CACHE_TTL, max_entries=CACHE_MAX_ENTRIES)
WINDOWS_CACHE = TTLCache(ttl_seconds=WINDOWS_CACHE_TTL, max_entries=CACHE_MAX_ENTRIES)
DASHBOARD_CACHE = TTLCache(ttl_seconds=DASHBOARD_CACHE_TTL, max_entries=CACHE_MAX_ENTRIES)


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _normalize_action(action: str | None) -> str:
    normalized = _clean(action).lower().replace("-", "_").replace(" ", "_")
    if normalized in ANALYZE_ACTIONS:
        return "analyze_log"
    if normalized in FETCH_WINDOWS_ACTIONS:
        return "fetch_windows_logs"
    if normalized in ANALYZE_WINDOWS_ACTIONS:
        return "analyze_windows_logs"
    if normalized in HISTORY_ACTIONS:
        return "get_history"
    if normalized in CHANNEL_ACTIONS:
        return "list_windows_channels"
    if normalized in DASHBOARD_ACTIONS:
        return "dashboard_overview"
    if normalized in CAPABILITY_ACTIONS:
        return "list_capabilities"
    return normalized


def _supported_actions() -> list[str]:
    return [
        "analyze_log",
        "fetch_windows_logs",
        "analyze_windows_logs",
        "get_history",
        "list_windows_channels",
        "dashboard_overview",
        "list_capabilities",
    ]


def _status_from_risk(risk: str) -> str:
    lowered = (risk or "").strip().lower()
    if lowered in {"critical", "high"}:
        return "high"
    if lowered in {"medium", "low"}:
        return "low"
    return "unknown"


def _parse_channels(raw: list[str] | str | None) -> list[str]:
    if raw is None:
        return ["Security", "System", "Application"]
    if isinstance(raw, str):
        parts = [chunk.strip() for chunk in raw.split(",")]
    else:
        parts = [str(chunk).strip() for chunk in raw]
    valid = [chunk for chunk in parts if chunk in VALID_CHANNELS]
    return valid or ["Security", "System", "Application"]


def _build_windows_log_text(log_payload: dict[str, Any]) -> str:
    logs = log_payload.get("logs") or []
    channels = ", ".join(log_payload.get("channels") or [])
    header = [
        f"Windows Event Logs ({len(logs)} entries)",
        f"Source: {log_payload.get('source', 'unknown')}",
        f"Fetched: {log_payload.get('fetched_at', '')}",
        f"Channels: {channels or 'unknown'}",
        "",
    ]
    lines: list[str] = []
    for event in logs:
        msg = str(event.get("message") or "").replace("\n", " ").strip()
        parts = [
            str(event.get("timestamp") or ""),
            f"EventID={event.get('event_id')}" if event.get("event_id") is not None else "",
            f"Channel={event.get('channel')}" if event.get("channel") else "",
            f"Severity={event.get('severity')}" if event.get("severity") else "",
            f"Source={event.get('source')}" if event.get("source") else "",
            f"Computer={event.get('computer')}" if event.get("computer") else "",
            f"Message={msg}" if msg else "",
        ]
        lines.append(" | ".join(part for part in parts if part))
    return "\n".join(header + lines)


def _with_cache_meta(payload: dict[str, Any], *, hit: bool, source: str, key: str, ttl_seconds: int) -> dict[str, Any]:
    enriched = copy.deepcopy(payload)
    result = enriched.get("result")
    if not isinstance(result, dict):
        result = {}
    result["cache"] = {
        "hit": hit,
        "source": source,
        "cacheKey": key,
        "ttlSeconds": ttl_seconds,
    }
    enriched["result"] = result
    return enriched


def _history_payload() -> dict[str, Any]:
    history = CYBER_AGENT.get_history()
    return {"history": history, "count": len(history)}


def health_payload() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "healthy",
        "agent": "cyber-soc-agent",
        "displayName": DISPLAY_NAME,
        "app": settings.app_name,
        "vt_configured": bool(settings.virustotal_api_key),
        "llm_configured": bool(settings.ollama_base_url) and bool(settings.ollama_model),
        "version": "2.0.0",
    }


def _dashboard_payload() -> dict[str, Any]:
    payload = _history_payload()
    history = payload["history"]
    total = len(history)
    high_critical = sum(1 for row in history if _status_from_risk(str(row.get("risk") or "")) == "high")
    low_medium = max(0, total - high_critical)
    avg_confidence = round(sum(int(row.get("confidence") or 0) for row in history) / total) if total else 0
    latest = history[0] if history else None
    return {
        "health": health_payload(),
        "stats": {
            "totalAnalyses": total,
            "highOrCritical": high_critical,
            "lowOrMedium": low_medium,
            "avgConfidence": avg_confidence,
        },
        "recentActivity": history[:5],
        "latestAnalysis": latest,
        "generatedAt": datetime.utcnow().isoformat() + "Z",
    }


def capabilities_response() -> CyberSocActionResponse:
    return CyberSocActionResponse(
        status="success",
        type="cyber_soc_capabilities",
        displayName=DISPLAY_NAME,
        message="Cyber AI SOC capabilities loaded.",
        summary="Supports security log analysis, Windows event retrieval, dashboard snapshots, and history access.",
        result={
            "actions": _supported_actions(),
            "requiredFields": {
                "analyze_log": ["log"],
                "fetch_windows_logs": [],
                "analyze_windows_logs": [],
                "get_history": [],
                "list_windows_channels": [],
                "dashboard_overview": [],
            },
            "optionalFields": {
                "analyze_log": ["forceRefresh"],
                "fetch_windows_logs": ["limit", "channels", "forceRefresh"],
                "analyze_windows_logs": ["limit", "channels", "forceRefresh"],
            },
            "uiSections": ["analyze", "dashboard", "windows_logs", "history", "mitre", "ioc", "virustotal"],
        },
    )


async def _run_analyze_log(req: CyberSocActionRequest) -> CyberSocActionResponse:
    log_text = _clean(req.log) or _clean(req.prompt)
    if not log_text:
        raise ValueError("Missing log input. Provide 'log' text for analysis.")
    if len(log_text) > 10_000:
        raise ValueError("Log input too large (max 10,000 chars).")

    analysis = await CYBER_AGENT.analyze(log_text)
    return CyberSocActionResponse(
        status="success",
        type="cyber_soc_analysis_result",
        displayName=DISPLAY_NAME,
        message="Security log analyzed successfully.",
        summary=str(analysis.get("summary") or analysis.get("reason") or "Analysis completed."),
        result={
            "analysis": analysis,
            "threat": analysis.get("threat"),
            "risk": analysis.get("risk"),
            "confidence": analysis.get("confidence"),
            "attackType": analysis.get("attack_type"),
            "iocs": analysis.get("indicators_found") or {},
            "mitre": analysis.get("mitre_mapping") or [],
            "vtResults": analysis.get("vt_results") or [],
        },
    )


def _run_fetch_windows_logs(req: CyberSocActionRequest) -> CyberSocActionResponse:
    limit = int(req.limit or 10)
    limit = max(1, min(limit, MAX_LOG_LIMIT))
    channels = _parse_channels(req.channels)
    logs_payload = fetch_event_logs(channels=channels, limit=limit)
    return CyberSocActionResponse(
        status="success",
        type="cyber_soc_windows_logs",
        displayName=DISPLAY_NAME,
        message=f"Fetched {logs_payload.get('count', 0)} Windows log entries.",
        summary="Windows event logs fetched successfully.",
        result=logs_payload,
    )


async def _run_analyze_windows_logs(req: CyberSocActionRequest) -> CyberSocActionResponse:
    logs_response = _run_fetch_windows_logs(req)
    logs_payload = logs_response.result or {}
    if not logs_payload.get("logs"):
        return CyberSocActionResponse(
            status="needs_input",
            type="cyber_soc_windows_analysis_result",
            displayName=DISPLAY_NAME,
            message="No logs available to analyze.",
            summary="Fetch logs first or adjust channels/limit.",
            result={"suggestedInputs": ["channels", "limit"]},
            error="empty_windows_logs",
        )

    windows_text = _build_windows_log_text(logs_payload)
    analysis = await CYBER_AGENT.analyze(windows_text)
    return CyberSocActionResponse(
        status="success",
        type="cyber_soc_windows_analysis_result",
        displayName=DISPLAY_NAME,
        message="Windows logs analyzed successfully.",
        summary=str(analysis.get("summary") or "Windows logs analyzed."),
        result={
            "analysis": analysis,
            "windowsLogs": logs_payload,
            "threat": analysis.get("threat"),
            "risk": analysis.get("risk"),
            "confidence": analysis.get("confidence"),
        },
    )


def _run_history() -> CyberSocActionResponse:
    payload = _history_payload()
    return CyberSocActionResponse(
        status="success",
        type="cyber_soc_history_result",
        displayName=DISPLAY_NAME,
        message=f"Loaded {payload['count']} past analyses.",
        summary="Analysis history loaded.",
        result=payload,
    )


def _run_channels() -> CyberSocActionResponse:
    return CyberSocActionResponse(
        status="success",
        type="cyber_soc_channels_result",
        displayName=DISPLAY_NAME,
        message="Windows channels loaded.",
        summary="Available channels: Security, System, Application.",
        result={
            "channels": sorted(VALID_CHANNELS),
            "description": {
                "Security": "Authentication, authorization, and audit events.",
                "System": "Operating-system and service level events.",
                "Application": "Application logs and runtime errors.",
            },
        },
    )


def _run_dashboard() -> CyberSocActionResponse:
    payload = _dashboard_payload()
    return CyberSocActionResponse(
        status="success",
        type="cyber_soc_dashboard_result",
        displayName=DISPLAY_NAME,
        message="Dashboard snapshot generated.",
        summary="SOC dashboard overview loaded.",
        result=payload,
    )


async def run_cyber_soc_action(req: CyberSocActionRequest) -> CyberSocActionResponse:
    action = _normalize_action(req.action)

    if action == "list_capabilities":
        return capabilities_response()

    if action not in set(_supported_actions()):
        return CyberSocActionResponse(
            status="failed",
            type="cyber_soc_result",
            displayName=DISPLAY_NAME,
            message=f"Unsupported action: {req.action}",
            summary="Use list_capabilities to view supported actions.",
            result={"supportedActions": _supported_actions()},
            error=f"unknown_action:{req.action}",
        )

    try:
        if action == "analyze_log":
            cache_key = make_cache_key(
                {
                    "action": action,
                    "log": _clean(req.log) or _clean(req.prompt),
                    "userId": _clean(req.userId),
                }
            )
            if not req.forceRefresh:
                cached = ANALYZE_CACHE.get(cache_key)
                if cached:
                    return CyberSocActionResponse.model_validate(
                        _with_cache_meta(cached, hit=True, source="memory", key=cache_key, ttl_seconds=ANALYZE_CACHE_TTL)
                    )

            response = await _run_analyze_log(req)
            payload = response.model_dump(mode="json")
            ANALYZE_CACHE.set(cache_key, payload)
            return CyberSocActionResponse.model_validate(
                _with_cache_meta(payload, hit=False, source="miss", key=cache_key, ttl_seconds=ANALYZE_CACHE_TTL)
            )

        if action == "fetch_windows_logs":
            cache_key = make_cache_key(
                {
                    "action": action,
                    "channels": _parse_channels(req.channels),
                    "limit": int(req.limit or 10),
                }
            )
            if not req.forceRefresh:
                cached = WINDOWS_CACHE.get(cache_key)
                if cached:
                    return CyberSocActionResponse.model_validate(
                        _with_cache_meta(cached, hit=True, source="memory", key=cache_key, ttl_seconds=WINDOWS_CACHE_TTL)
                    )

            response = _run_fetch_windows_logs(req)
            payload = response.model_dump(mode="json")
            WINDOWS_CACHE.set(cache_key, payload)
            return CyberSocActionResponse.model_validate(
                _with_cache_meta(payload, hit=False, source="miss", key=cache_key, ttl_seconds=WINDOWS_CACHE_TTL)
            )

        if action == "analyze_windows_logs":
            cache_key = make_cache_key(
                {
                    "action": action,
                    "channels": _parse_channels(req.channels),
                    "limit": int(req.limit or 10),
                    "userId": _clean(req.userId),
                }
            )
            if not req.forceRefresh:
                cached = ANALYZE_CACHE.get(cache_key)
                if cached:
                    return CyberSocActionResponse.model_validate(
                        _with_cache_meta(cached, hit=True, source="memory", key=cache_key, ttl_seconds=ANALYZE_CACHE_TTL)
                    )

            response = await _run_analyze_windows_logs(req)
            payload = response.model_dump(mode="json")
            if response.status in {"success", "partial_success"}:
                ANALYZE_CACHE.set(cache_key, payload)
            return CyberSocActionResponse.model_validate(
                _with_cache_meta(payload, hit=False, source="miss", key=cache_key, ttl_seconds=ANALYZE_CACHE_TTL)
            )

        if action == "get_history":
            return _run_history()

        if action == "list_windows_channels":
            return _run_channels()

        if action == "dashboard_overview":
            cache_key = make_cache_key({"action": action, "userId": _clean(req.userId), "chatId": _clean(req.chatId)})
            if not req.forceRefresh:
                cached = DASHBOARD_CACHE.get(cache_key)
                if cached:
                    return CyberSocActionResponse.model_validate(
                        _with_cache_meta(cached, hit=True, source="memory", key=cache_key, ttl_seconds=DASHBOARD_CACHE_TTL)
                    )

            response = _run_dashboard()
            payload = response.model_dump(mode="json")
            DASHBOARD_CACHE.set(cache_key, payload)
            return CyberSocActionResponse.model_validate(
                _with_cache_meta(payload, hit=False, source="miss", key=cache_key, ttl_seconds=DASHBOARD_CACHE_TTL)
            )

        return capabilities_response()
    except ValueError as exc:
        return CyberSocActionResponse(
            status="needs_input",
            type="cyber_soc_result",
            displayName=DISPLAY_NAME,
            message=str(exc),
            summary="Please correct the request payload and retry.",
            error=str(exc),
        )
    except Exception as exc:
        logger.error("cyber_soc_action_failed", exc_info=True)
        return CyberSocActionResponse(
            status="failed",
            type="cyber_soc_result",
            displayName=DISPLAY_NAME,
            message="Cyber AI SOC failed to process this request.",
            summary="Retry with a cleaner payload. Use list_capabilities for valid action formats.",
            error=f"cyber_soc_failed:{exc.__class__.__name__}",
        )
