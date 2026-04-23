from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx


REQUEST_TIMEOUT_SECONDS = float((os.getenv("DATA_ANALYST_LLM_TIMEOUT_SECONDS") or "22").strip())
PREFERRED_PROVIDER = (os.getenv("DATA_ANALYST_LLM_PROVIDER") or "auto").strip().lower()

GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()
GEMINI_MODEL = (
    os.getenv("GEMINI_MODEL")
    or os.getenv("GEMINI_MODEL_FLASH")
    or os.getenv("GEMINI_MODEL_PRO")
    or "gemini-2.5-flash"
).strip()

OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_MODEL = (os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip()

ANTHROPIC_API_KEY = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
ANTHROPIC_MODEL = (os.getenv("ANTHROPIC_MODEL") or "claude-3-5-haiku-latest").strip()

OLLAMA_BASE_URL = (os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").strip().rstrip("/")
OLLAMA_MODEL = (os.getenv("OLLAMA_MODEL") or "qwen2.5:7b").strip()


def _extract_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    for pattern in (r"```json\s*([\s\S]*?)\s*```", r"```\s*([\s\S]*?)\s*```", r"(\{[\s\S]*\})"):
        match = re.search(pattern, text)
        if not match:
            continue
        snippet = match.group(1)
        try:
            parsed = json.loads(snippet)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _provider_order() -> list[str]:
    if PREFERRED_PROVIDER in {"disabled", "none", "off"}:
        return []
    if PREFERRED_PROVIDER in {"gemini", "openai", "anthropic", "ollama"}:
        return [PREFERRED_PROVIDER]
    return ["gemini", "openai", "anthropic", "ollama"]


def _fallback_insight(
    *,
    label: str,
    goal: str | None,
    anomaly: dict[str, Any] | None,
) -> dict[str, Any]:
    anomaly = anomaly or {}
    status = anomaly.get("status") or "ok"
    indices = anomaly.get("indices") or []
    flagged_values = anomaly.get("flaggedValues") or []
    goal_text = (goal or "").strip()

    if status == "anomaly":
        summary = (
            f"Anomaly detected in {label}. "
            f"Flagged indices: {indices}. Values: {flagged_values}."
        )
        recommendations = [
            "Validate source data for the flagged indices.",
            "Check ETL transformations around the anomaly window.",
            "Confirm whether spikes are expected business events or true errors.",
        ]
        risk_level = "high"
    else:
        summary = f"No strong anomaly signal detected in {label}."
        recommendations = [
            "Continue monitoring this metric over time.",
            "Add threshold alerts for early anomaly detection.",
            "Track contextual business events for better interpretation.",
        ]
        risk_level = "low"

    if goal_text:
        summary = f"{summary} Goal focus: {goal_text[:140]}."

    return {
        "summary": summary,
        "recommendations": recommendations,
        "riskLevel": risk_level,
        "nextInputs": [
            "historical_baseline_window",
            "known_business_events",
            "segment_breakdown",
        ],
        "provider": "fallback",
    }


def _analysis_prompt(label: str, goal: str | None, anomaly: dict[str, Any] | None, data: list[float] | None) -> str:
    return json.dumps(
        {
            "task": "Generate concise analytics insight in strict JSON.",
            "outputSchema": {
                "summary": "string",
                "recommendations": ["string"],
                "riskLevel": "low|medium|high",
                "nextInputs": ["string"],
            },
            "context": {
                "label": label,
                "goal": (goal or "").strip(),
                "anomaly": anomaly or {},
                "dataPreview": (data or [])[:40],
                "dataPointCount": len(data or []),
            },
            "rules": [
                "Do not output markdown.",
                "Do not include keys outside outputSchema.",
                "Keep summary under 300 chars.",
                "Recommendations should be practical and specific.",
            ],
        },
        ensure_ascii=True,
    )


def _normalize_llm_json(raw: dict[str, Any], fallback: dict[str, Any], provider: str) -> dict[str, Any]:
    summary = str(raw.get("summary") or fallback["summary"]).strip()
    risk = str(raw.get("riskLevel") or fallback["riskLevel"]).strip().lower()
    if risk not in {"low", "medium", "high"}:
        risk = fallback["riskLevel"]

    recommendations_raw = raw.get("recommendations")
    if isinstance(recommendations_raw, list):
        recommendations = [str(item).strip() for item in recommendations_raw if str(item).strip()]
    else:
        recommendations = []
    if not recommendations:
        recommendations = list(fallback["recommendations"])

    next_inputs_raw = raw.get("nextInputs")
    if isinstance(next_inputs_raw, list):
        next_inputs = [str(item).strip() for item in next_inputs_raw if str(item).strip()]
    else:
        next_inputs = []
    if not next_inputs:
        next_inputs = list(fallback["nextInputs"])

    return {
        "summary": summary,
        "recommendations": recommendations[:6],
        "riskLevel": risk,
        "nextInputs": next_inputs[:6],
        "provider": provider,
    }


async def _call_gemini(prompt: str) -> str:
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "maxOutputTokens": 700,
        },
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
            params={"key": GEMINI_API_KEY},
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
    parts = body.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "\n".join(str(part.get("text", "")) for part in parts if isinstance(part, dict)).strip()


async def _call_openai(prompt: str) -> str:
    payload = {
        "model": OPENAI_MODEL,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "Return strict JSON only."},
            {"role": "user", "content": prompt},
        ],
    }
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
    return str(body.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()


async def _call_anthropic(prompt: str) -> str:
    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 700,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
    parts = body.get("content", [])
    text_parts = [str(part.get("text", "")) for part in parts if isinstance(part, dict)]
    return "\n".join(text_parts).strip()


async def _call_ollama(prompt: str) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2},
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
    return str(body.get("response", "")).strip()


async def generate_analysis(
    *,
    label: str,
    goal: str | None,
    anomaly: dict[str, Any] | None,
    data: list[float] | None,
) -> dict[str, Any]:
    fallback = _fallback_insight(label=label, goal=goal, anomaly=anomaly)
    prompt = _analysis_prompt(label=label, goal=goal, anomaly=anomaly, data=data)

    for provider in _provider_order():
        try:
            if provider == "gemini":
                if not GEMINI_API_KEY:
                    continue
                raw_text = await _call_gemini(prompt)
            elif provider == "openai":
                if not OPENAI_API_KEY:
                    continue
                raw_text = await _call_openai(prompt)
            elif provider == "anthropic":
                if not ANTHROPIC_API_KEY:
                    continue
                raw_text = await _call_anthropic(prompt)
            elif provider == "ollama":
                raw_text = await _call_ollama(prompt)
            else:
                continue

            parsed = _extract_json(raw_text)
            if parsed:
                return _normalize_llm_json(parsed, fallback, provider)
        except Exception:
            continue

    return fallback
