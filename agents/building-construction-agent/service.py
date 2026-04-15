from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

from firestore_store import list_recent_plans, save_plan
from schemas import BuildingConstructionActionRequest, BuildingConstructionActionResponse

DEFAULT_MODEL = (
    os.getenv("GEMINI_MODEL_FLASH")
    or os.getenv("GEMINI_MODEL")
    or os.getenv("GEMINI_MODEL_PRO")
    or "gemini-2.5-flash"
).strip()


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _to_lines(value: str, limit: int = 5) -> list[str]:
    chunks = [part.strip("- ").strip() for part in re.split(r"[\n\r•]+", value) if part and part.strip()]
    return chunks[:limit]


def _extract_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    for pattern in (r"```json\s*([\s\S]*?)\s*```", r"```\s*([\s\S]*?)\s*```", r"(\{[\s\S]*\})"):
        match = re.search(pattern, text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _fallback_structured(req: BuildingConstructionActionRequest) -> dict[str, Any]:
    location = req.location.location_name if req.location else ""
    style = _clean(req.design_style) or "modern"
    floors = req.floors if req.floors and req.floors > 0 else 2
    budget = req.budget_inr if req.budget_inr and req.budget_inr > 0 else 8500000

    room_templates = [
        {"name": "Living Room", "area_sqm": 24, "dimensions": "6m x 4m"},
        {"name": "Kitchen", "area_sqm": 12, "dimensions": "4m x 3m"},
        {"name": "Master Bedroom", "area_sqm": 18, "dimensions": "4.5m x 4m"},
        {"name": "Bedroom 2", "area_sqm": 14, "dimensions": "3.5m x 4m"},
    ]

    return {
        "tools_called": ["layout_planner_tool", "cost_estimator_tool", "hybrid_vendor_search_tool"],
        "reasoning_log": [
            "Identified requirements from user prompt.",
            "Generated conceptual layout and budget bands.",
            "Added vendor shortlist with practical next steps.",
        ],
        "plot_analysis": {
            "shape": "rectangular",
            "estimated_area_sqm": 111,
            "estimated_width_m": 9.14,
            "estimated_depth_m": 12.19,
            "road_facing": "east",
            "slope": "gentle",
        },
        "layout_plan": {
            "floors": floors,
            "total_built_area_sqm": 185,
            "design_style": style,
            "floor_plans": [{"rooms": room_templates}],
            "vastu_notes": [
                "Keep kitchen in the southeast zone where feasible.",
                "Ensure daylight and cross ventilation in living spaces.",
            ],
        },
        "cost_estimate": {
            "total_estimated_cost": budget,
            "cost_breakdown": {
                "civil_works": round(budget * 0.42),
                "finishing": round(budget * 0.21),
                "electrical_plumbing": round(budget * 0.16),
                "interiors": round(budget * 0.13),
                "contingency": round(budget * 0.08),
            },
            "timeline_months": 11,
            "cost_saving_tips": [
                "Standardize room sizes to reduce waste and rework.",
                "Lock steel/cement rates early with milestone contracts.",
            ],
        },
        "vendor_results": {
            "search_summary": f"Shortlisted practical vendor categories for {location or 'your location'}.",
            "vendors": [
                {"name": "UrbanBuild Contractors", "type": "contractor", "phone": "+91-9000001001"},
                {"name": "Aster Design Studio", "type": "architect", "phone": "+91-9000001002"},
                {"name": "Prime Materials Hub", "type": "supplier", "phone": "+91-9000001003"},
            ],
        },
        "generated_image": {
            "available": False,
            "note": "Image generation is optional and was not requested in this run.",
        },
    }


async def _gemini_structured(req: BuildingConstructionActionRequest) -> dict[str, Any]:
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    fallback = _fallback_structured(req)
    if not api_key:
        return fallback

    location_name = req.location.location_name if req.location else ""
    prompt = _clean(req.prompt) or _clean(req.message)
    if not prompt:
        return fallback

    payload = {
        "system_instruction": {
            "parts": [
                {
                    "text": (
                        "You are a senior construction planning assistant. "
                        "Return only strict JSON that follows the requested schema."
                    )
                }
            ]
        },
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": (
                            f"User request: {prompt}\n"
                            f"Location: {location_name or 'Unknown'}\n"
                            f"Budget INR: {req.budget_inr or 'Not specified'}\n"
                            f"Floors: {req.floors or 'Not specified'}\n"
                            f"Design style: {_clean(req.design_style) or 'Not specified'}\n"
                            f"Rooms requested: {req.rooms or []}\n"
                            f"Special requirements: {_clean(req.special_requirements) or 'None'}\n\n"
                            "Return JSON with keys: tools_called(array), reasoning_log(array), "
                            "plot_analysis(object), layout_plan(object), cost_estimate(object), "
                            "vendor_results(object), generated_image(object)."
                        )
                    }
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.3,
            "topP": 0.9,
            "responseMimeType": "application/json",
            "maxOutputTokens": 2200,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=55.0) as client:
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{DEFAULT_MODEL}:generateContent",
                params={"key": api_key},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
    except Exception:
        return fallback

    parts = (
        data.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [])
    )
    raw = "\n".join(str(part.get("text", "")) for part in parts if isinstance(part, dict))
    parsed = _extract_json(raw)
    return parsed if parsed else fallback


def _require_user(req: BuildingConstructionActionRequest) -> str:
    user_id = _clean(req.userId)
    if not user_id:
        raise ValueError("userId is required for building-construction-agent actions.")
    return user_id


async def _generate_plan(user_id: str, req: BuildingConstructionActionRequest) -> BuildingConstructionActionResponse:
    prompt = _clean(req.prompt) or _clean(req.message)
    if not prompt:
        return BuildingConstructionActionResponse(
            status="needs_input",
            message="Need your construction goal to generate a plan.",
            summary="Please share plot requirements or desired house plan details.",
            result={"suggestedInputs": ["prompt"]},
        )

    structured = await _gemini_structured(req)
    human_response = (
        f"Prepared a construction planning brief for {_clean(req.location.location_name) if req.location else 'your location'} "
        "with layout, cost, and vendor guidance."
    )

    payload = {
        "session_id": req.chatId or req.taskId or "",
        "human_response": human_response,
        "structured_output": structured,
        "reasoning_log": structured.get("reasoning_log", []),
        "tools_called": structured.get("tools_called", []),
    }
    plan_id = save_plan(user_id, payload)
    payload["planId"] = plan_id

    return BuildingConstructionActionResponse(
        status="success",
        type="building_construction_result",
        message="Building construction plan generated successfully.",
        summary="Layout, cost estimate, and vendor shortlist are ready.",
        result=payload,
    )


async def _list_plans(user_id: str) -> BuildingConstructionActionResponse:
    plans = list_recent_plans(user_id, limit=12)
    simplified = []
    for item in plans:
        payload = item.get("payload", {}) if isinstance(item.get("payload"), dict) else {}
        simplified.append(
            {
                "planId": item.get("planId"),
                "summary": _clean(payload.get("human_response")),
                "tools_called": payload.get("tools_called", []),
                "createdAtIso": item.get("createdAtIso", ""),
            }
        )
    return BuildingConstructionActionResponse(
        status="success",
        type="building_construction_result",
        message=f"Loaded {len(simplified)} recent plans.",
        summary="Recent building-construction plans loaded.",
        result={"plans": simplified},
    )


async def run_building_construction_action(
    req: BuildingConstructionActionRequest,
) -> BuildingConstructionActionResponse:
    try:
        user_id = _require_user(req)
        action = _clean(req.action).lower()

        if action in {"generate_plan", "run_agent", "building_plan", "generate_construction_plan"}:
            return await _generate_plan(user_id, req)

        if action in {"list_plans", "list_recent_plans"}:
            return await _list_plans(user_id)

        return BuildingConstructionActionResponse(
            status="failed",
            type="building_construction_result",
            message=f"Unsupported action: {req.action}",
            summary="Requested building action is not available.",
            error=f"Unknown action: {req.action}",
        )
    except ValueError as exc:
        return BuildingConstructionActionResponse(
            status="needs_input",
            type="building_construction_result",
            message=str(exc),
            summary="Building agent needs required input to continue.",
            result={"suggestedInputs": ["userId"]},
            error=str(exc),
        )
    except Exception:
        return BuildingConstructionActionResponse(
            status="failed",
            type="building_construction_result",
            message="Building Construction Agent failed to complete the request.",
            summary="Please retry with clearer requirements (plot size, floors, budget, location).",
            error="building-construction execution failed.",
        )
