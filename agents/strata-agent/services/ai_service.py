"""AI helpers for strata-agent (Gemini first, deterministic fallback)."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

GEMINI_MODEL = os.getenv("GEMINI_MODEL_PRO", "gemini-2.5-pro")
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _gemini_api_key() -> str:
    return (os.getenv("GEMINI_API_KEY") or "").strip()


async def generate_insight_block(payload: dict[str, Any]) -> dict[str, str]:
    prompt = (
        "You are STRATA, a financial analytics copilot.\n"
        "Analyze the data below and return compact JSON with keys: insight, cause, action, risk_level.\n"
        "Use only the provided numbers.\n\n"
        f"DATA:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )

    parsed = await _ask_model_json(prompt)
    if parsed:
        return {
            "insight": str(parsed.get("insight") or "").strip() or "Latest performance snapshot generated.",
            "cause": str(parsed.get("cause") or "").strip() or "Derived from reported revenue, expense, and margin mix.",
            "action": str(parsed.get("action") or "").strip() or "Review largest expense buckets and margin trend.",
            "risk_level": str(parsed.get("risk_level") or "medium").strip().lower(),
        }

    summary = payload.get("summary", {})
    revenue = float(summary.get("revenue") or 0.0)
    expenses = float(summary.get("expenses") or 0.0)
    margin = float(summary.get("margin") or 0.0)
    insight = "Margin remains healthy with stable operating performance."
    risk = "low"
    if margin < 10:
        insight = "Margin is under pressure and needs immediate cost optimization."
        risk = "high"
    return {
        "insight": insight,
        "cause": f"Revenue is {revenue:,.0f} while operating expenses are {expenses:,.0f}.",
        "action": "Prioritize cost centers with the fastest growth and protect high-yield revenue streams.",
        "risk_level": risk,
    }


async def answer_query(question: str, context: dict[str, Any]) -> str:
    prompt = (
        "You are STRATA.\n"
        "Answer the user question using the provided financial context only.\n"
        "Keep answer concise and numeric.\n\n"
        f"QUESTION: {question}\n\n"
        f"CONTEXT:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
    )
    text = await _ask_model_text(prompt)
    if text:
        return text
    summary = context.get("summary", {})
    return (
        "I could not reach the AI model, so here is a deterministic answer: "
        f"revenue={summary.get('revenue')}, expenses={summary.get('expenses')}, margin={summary.get('margin')}%."
    )


async def summarize_attachments(report_name: str, processed_files: list[dict[str, Any]]) -> str:
    prompt = (
        "Summarize these uploaded business files for a financial decision dashboard.\n"
        "Return a short, executive summary with 4-6 bullets.\n\n"
        f"REPORT NAME: {report_name}\n\n"
        f"FILES:\n{json.dumps(processed_files, ensure_ascii=False, indent=2)}"
    )
    text = await _ask_model_text(prompt)
    if text:
        return text
    lines = [f"Summary generated without model for report '{report_name}':"]
    for item in processed_files[:5]:
        lines.append(f"- {item.get('name')}: parsed successfully.")
    return "\n".join(lines)


async def _ask_model_json(prompt: str) -> dict[str, Any] | None:
    text = await _ask_model_text(prompt)
    if not text:
        return None
    try:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
        return json.loads(cleaned)
    except Exception:
        return None


async def _ask_model_text(prompt: str) -> str | None:
    api_key = _gemini_api_key()
    if not api_key:
        return None

    endpoint = GEMINI_ENDPOINT.format(model=GEMINI_MODEL)
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(f"{endpoint}?key={api_key}", json=body)
            response.raise_for_status()
    except Exception:
        return None

    payload = response.json()
    candidates = payload.get("candidates") or []
    if not candidates:
        return None
    parts = (((candidates[0] or {}).get("content") or {}).get("parts")) or []
    texts = [str(part.get("text") or "").strip() for part in parts if isinstance(part, dict)]
    merged = "\n".join([text for text in texts if text])
    return merged or None
