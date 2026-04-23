from __future__ import annotations

import os
from typing import Any

from anomaly import detect_anomalies, parse_numeric_series
from cache import TTLCache, make_cache_key
from llm_client import generate_analysis
from schemas import DataAnalystActionRequest, DataAnalystActionResponse
from store import get_cached_result, save_analysis_log, save_cached_result, validated_uid


DISPLAY_NAME = os.getenv("DATA_ANALYST_DISPLAY_NAME", "Data Analyst Agent")
DATASET_MAX_POINTS = int((os.getenv("DATA_ANALYST_DATASET_MAX_POINTS") or "5000").strip() or "5000")
CACHE_TTL_SECONDS = int((os.getenv("DATA_ANALYST_CACHE_TTL_SECONDS") or "900").strip() or "900")
CACHE_MAX_ENTRIES = int((os.getenv("DATA_ANALYST_CACHE_MAX_ENTRIES") or "512").strip() or "512")
MEMORY_CACHE = TTLCache(ttl_seconds=CACHE_TTL_SECONDS, max_entries=CACHE_MAX_ENTRIES)


MONITOR_ACTIONS = {
    "monitor",
    "monitor_data",
    "analyze_data",
    "detect_anomaly",
    "detect_anomalies",
    "run_data_analyst",
    "run",
    "analyze",
}
AUTONOMOUS_ACTIONS = {
    "autonomous",
    "autonomous_analysis",
    "autonomous_monitor",
    "goal_analysis",
    "goal_based_analysis",
    "strategize",
    "plan",
}
CAPABILITY_ACTIONS = {"capabilities", "list_capabilities", "help", "actions"}


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _normalize_action(action: str) -> str:
    normalized = _clean(action).lower().replace("-", "_").replace(" ", "_")
    if normalized in MONITOR_ACTIONS:
        return "monitor"
    if normalized in AUTONOMOUS_ACTIONS:
        return "autonomous"
    if normalized in CAPABILITY_ACTIONS:
        return "list_capabilities"
    return normalized


def _resolve_goal(req: DataAnalystActionRequest) -> str:
    return _clean(req.goal) or _clean(req.prompt)


def _supported_actions() -> list[str]:
    return ["monitor", "autonomous", "list_capabilities"]


def _monitor_cache_payload(req: DataAnalystActionRequest, label: str, data: list[float]) -> dict[str, Any]:
    return {
        "action": "monitor",
        "userId": _clean(req.userId),
        "chatId": _clean(req.chatId),
        "label": label,
        "data": data,
    }


def _autonomous_cache_payload(req: DataAnalystActionRequest, label: str, data: list[float] | None, goal: str) -> dict[str, Any]:
    return {
        "action": "autonomous",
        "userId": _clean(req.userId),
        "chatId": _clean(req.chatId),
        "label": label,
        "goal": goal,
        "data": data or [],
    }


def _cache_enabled(action: str) -> bool:
    return action in {"monitor", "autonomous"}


def _attach_cache_meta(
    response: DataAnalystActionResponse,
    *,
    cache_hit: bool,
    cache_source: str,
    cache_key: str,
) -> DataAnalystActionResponse:
    payload = dict(response.result or {})
    payload["cache"] = {
        "hit": cache_hit,
        "source": cache_source,
        "cacheKey": cache_key,
        "ttlSeconds": CACHE_TTL_SECONDS,
    }
    response.result = payload
    return response


def _capabilities_response() -> DataAnalystActionResponse:
    return DataAnalystActionResponse(
        status="success",
        type="data_analyst_capabilities",
        displayName=DISPLAY_NAME,
        message="Data Analyst capabilities loaded.",
        summary="Supports monitor, autonomous, and streaming monitor workflows.",
        result={
            "actions": _supported_actions(),
            "actionAliases": {
                "monitor": sorted(MONITOR_ACTIONS),
                "autonomous": sorted(AUTONOMOUS_ACTIONS),
            },
            "requiredFields": {
                "monitor": ["data"],
                "autonomous": ["goal or prompt"],
            },
            "optionalFields": {
                "monitor": ["label", "forceRefresh"],
                "autonomous": ["data", "label", "forceRefresh"],
            },
        },
    )


async def _run_monitor(req: DataAnalystActionRequest) -> DataAnalystActionResponse:
    label = _clean(req.label) or "dataset"
    data = parse_numeric_series(req.data, DATASET_MAX_POINTS)
    anomaly = detect_anomalies(data, label=label)

    insight = await generate_analysis(
        label=label,
        goal=None,
        anomaly=anomaly,
        data=data,
    )
    summary = str(insight.get("summary") or anomaly["message"]).strip()
    message = (
        f"Anomaly detected for {label}."
        if anomaly["status"] == "anomaly"
        else f"{label} looks stable."
    )

    result_payload = {
        "label": label,
        "dataPointCount": len(data),
        "dataProfile": anomaly["stats"],
        "anomaly": anomaly,
        "insight": insight,
        "recommendedActions": insight.get("recommendations") or [],
    }

    return DataAnalystActionResponse(
        status="success",
        type="data_monitor_result",
        displayName=DISPLAY_NAME,
        message=message,
        summary=summary,
        result=result_payload,
    )


async def _run_autonomous(req: DataAnalystActionRequest) -> DataAnalystActionResponse:
    goal = _resolve_goal(req)
    if not goal:
        return DataAnalystActionResponse(
            status="needs_input",
            type="data_autonomous_result",
            displayName=DISPLAY_NAME,
            message="I need your goal to run autonomous analysis.",
            summary="Provide goal or prompt to continue.",
            result={"suggestedInputs": ["goal or prompt"]},
            error="missing_goal",
        )

    label = _clean(req.label) or "dataset"
    parsed_data: list[float] | None = None
    anomaly: dict[str, Any] | None = None
    if req.data is not None:
        parsed_data = parse_numeric_series(req.data, DATASET_MAX_POINTS)
        anomaly = detect_anomalies(parsed_data, label=label)

    insight = await generate_analysis(
        label=label,
        goal=goal,
        anomaly=anomaly,
        data=parsed_data,
    )

    if anomaly and anomaly.get("status") == "anomaly":
        message = "Autonomous analysis completed with anomaly findings."
    elif anomaly:
        message = "Autonomous analysis completed and no strong anomaly signal was found."
    else:
        message = "Autonomous strategy generated. Share data to run anomaly checks."

    result_payload = {
        "goal": goal,
        "label": label,
        "dataProvided": parsed_data is not None,
        "anomaly": anomaly,
        "insight": insight,
        "nextInputs": insight.get("nextInputs") or [],
    }

    return DataAnalystActionResponse(
        status="success",
        type="data_autonomous_result",
        displayName=DISPLAY_NAME,
        message=message,
        summary=str(insight.get("summary") or message),
        result=result_payload,
    )


async def run_data_analyst_action(req: DataAnalystActionRequest) -> DataAnalystActionResponse:
    try:
        normalized_action = _normalize_action(req.action)
        uid = validated_uid(req.userId)

        if normalized_action == "list_capabilities":
            response = _capabilities_response()
            save_analysis_log(uid, response.model_dump(mode="json"), response.status)
            return response

        if normalized_action not in {"monitor", "autonomous"}:
            response = DataAnalystActionResponse(
                status="failed",
                type="data_analyst_result",
                displayName=DISPLAY_NAME,
                message=f"Unsupported action: {req.action}",
                summary="Use monitor, autonomous, or list_capabilities.",
                result={"supportedActions": _supported_actions()},
                error=f"unknown_action:{req.action}",
            )
            save_analysis_log(uid, response.model_dump(mode="json"), response.status)
            return response

        cache_key = ""
        if _cache_enabled(normalized_action):
            if normalized_action == "monitor":
                label = _clean(req.label) or "dataset"
                parsed_data = parse_numeric_series(req.data, DATASET_MAX_POINTS)
                cache_key = make_cache_key(_monitor_cache_payload(req, label, parsed_data))
            else:
                label = _clean(req.label) or "dataset"
                parsed_data = (
                    parse_numeric_series(req.data, DATASET_MAX_POINTS)
                    if req.data is not None
                    else None
                )
                cache_key = make_cache_key(
                    _autonomous_cache_payload(req, label, parsed_data, _resolve_goal(req))
                )

        if normalized_action == "autonomous" and not _resolve_goal(req):
            response = DataAnalystActionResponse(
                status="needs_input",
                type="data_autonomous_result",
                displayName=DISPLAY_NAME,
                message="I need your goal to run autonomous analysis.",
                summary="Provide goal or prompt to continue.",
                result={"suggestedInputs": ["goal or prompt"]},
                error="missing_goal",
            )
            save_analysis_log(uid, response.model_dump(mode="json"), response.status)
            return response

        if cache_key and not req.forceRefresh:
            memory_hit = MEMORY_CACHE.get(cache_key)
            if memory_hit:
                response = DataAnalystActionResponse.model_validate(memory_hit)
                return _attach_cache_meta(
                    response,
                    cache_hit=True,
                    cache_source="memory",
                    cache_key=cache_key,
                )

            cached_payload, source = get_cached_result(uid, cache_key, CACHE_TTL_SECONDS)
            if cached_payload and source:
                MEMORY_CACHE.set(cache_key, cached_payload)
                response = DataAnalystActionResponse.model_validate(cached_payload)
                return _attach_cache_meta(
                    response,
                    cache_hit=True,
                    cache_source=source,
                    cache_key=cache_key,
                )

        if normalized_action == "monitor":
            response = await _run_monitor(req)
        else:
            response = await _run_autonomous(req)

        if cache_key and response.status in {"success", "partial_success"}:
            response = _attach_cache_meta(
                response,
                cache_hit=False,
                cache_source="miss",
                cache_key=cache_key,
            )
            payload = response.model_dump(mode="json")
            MEMORY_CACHE.set(cache_key, payload)
            save_cached_result(uid, cache_key, payload)

        save_analysis_log(uid, response.model_dump(mode="json"), response.status)
        return response

    except ValueError as exc:
        return DataAnalystActionResponse(
            status="needs_input",
            type="data_analyst_result",
            displayName=DISPLAY_NAME,
            message=str(exc),
            summary="Please correct the request payload and try again.",
            error=str(exc),
        )
    except Exception as exc:
        return DataAnalystActionResponse(
            status="failed",
            type="data_analyst_result",
            displayName=DISPLAY_NAME,
            message="Data Analyst Agent failed to process this request.",
            summary="Retry with a cleaner payload. If this repeats, use list_capabilities for valid actions.",
            error=f"data_analyst_failed:{exc.__class__.__name__}",
        )
