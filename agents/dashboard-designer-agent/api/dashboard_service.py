from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter
from typing import Any, Dict, List, Sequence, Tuple

import httpx

from .schemas import (
    DashboardAnalysis,
    DashboardChart,
    DashboardDesignerArtifact,
    DashboardKPI,
    DashboardRequestSummary,
    DashboardSchema,
    DashboardTable,
    DashboardTableColumn,
    DashboardThreshold,
)

DOMAIN_LIBRARY: Dict[str, Dict[str, str]] = {
    "sales": {"title": "Sales Performance Dashboard", "audience": "sales leaders", "focus": "revenue growth"},
    "marketing": {"title": "Marketing Growth Dashboard", "audience": "marketing teams", "focus": "campaign efficiency"},
    "support": {"title": "Support Operations Dashboard", "audience": "support managers", "focus": "service quality"},
    "finance": {"title": "Finance Performance Dashboard", "audience": "finance leaders", "focus": "margin and cash health"},
    "product": {"title": "Product Analytics Dashboard", "audience": "product teams", "focus": "adoption and retention"},
    "operations": {"title": "Operations Control Dashboard", "audience": "operations managers", "focus": "throughput and SLAs"},
    "ecommerce": {"title": "Ecommerce Revenue Dashboard", "audience": "growth teams", "focus": "orders and basket quality"},
    "engineering": {"title": "Engineering Delivery Dashboard", "audience": "engineering leaders", "focus": "release health"},
}

DEFAULT_DOMAIN = {"title": "Dashboard Designer Output", "audience": "operators", "focus": "decision making"}


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\r", " ")).strip()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "metric"


def _seed(*parts: str) -> int:
    digest = hashlib.sha256("||".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:10], 16)


def _format_compact(value: float) -> str:
    abs_value = abs(value)
    if abs_value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if abs_value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"


def _format_value(value: float, fmt: str) -> str:
    if fmt == "currency":
        return f"${_format_compact(value)}"
    if fmt == "percent":
        return f"{value:.1f}%"
    if fmt == "ratio":
        return f"{value:.1f}x"
    return _format_compact(value)


def _delta(current: float, previous: float) -> float:
    if previous == 0:
        return current
    return ((current - previous) / abs(previous)) * 100.0


def _infer_domain(text: str) -> str:
    lower = text.lower()
    for domain in DOMAIN_LIBRARY:
        if domain in lower:
            return domain
    if any(word in lower for word in ("mrr", "arr", "margin", "cash", "finance")):
        return "finance"
    if any(word in lower for word in ("campaign", "lead", "channel", "ad ", "marketing")):
        return "marketing"
    if any(word in lower for word in ("ticket", "sla", "csat", "support", "queue")):
        return "support"
    if any(word in lower for word in ("deploy", "incident", "latency", "uptime", "engineering")):
        return "engineering"
    if any(word in lower for word in ("order", "checkout", "refund", "ecommerce", "basket")):
        return "ecommerce"
    if any(word in lower for word in ("feature", "activation", "retention", "product", "users")):
        return "product"
    if any(word in lower for word in ("throughput", "ops", "warehouse", "delivery", "operations")):
        return "operations"
    return "sales" if "revenue" in lower else "general"


def _infer_horizon(text: str) -> str:
    lower = text.lower()
    if any(word in lower for word in ("daily", "today", "day")):
        return "daily"
    if any(word in lower for word in ("weekly", "week")):
        return "weekly"
    if any(word in lower for word in ("quarter", "q1", "q2", "q3", "q4")):
        return "quarterly"
    if any(word in lower for word in ("annual", "year", "yoy")):
        return "annual"
    return "monthly"


def _infer_focus(text: str, fallback: str) -> str:
    lower = text.lower()
    if "trend" in lower or "forecast" in lower:
        return "trend analysis"
    if "alert" in lower or "threshold" in lower:
        return "risk monitoring"
    if "compare" in lower:
        return "comparison"
    return fallback


def _find_manual_data(payload: Any, context: Dict[str, Any]) -> Any:
    if payload is not None:
        return payload
    for key in ("manualData", "manual_data", "data", "dataset"):
        if context.get(key) is not None:
            return context.get(key)
    return None


def _normalize_rows(manual_data: Any) -> Tuple[List[Dict[str, Any]], str]:
    if isinstance(manual_data, list):
        return [row for row in manual_data if isinstance(row, dict)], "list_rows"
    if isinstance(manual_data, dict):
        if isinstance(manual_data.get("rows"), list):
            return [row for row in manual_data["rows"] if isinstance(row, dict)], "dict_rows"
        if isinstance(manual_data.get("data"), list):
            return [row for row in manual_data["data"] if isinstance(row, dict)], "dict_data"
        if all(isinstance(value, list) for value in manual_data.values()):
            keys = list(manual_data.keys())
            row_count = max((len(value) for value in manual_data.values()), default=0)
            rows = []
            for idx in range(row_count):
                rows.append({key: manual_data[key][idx] for key in keys if idx < len(manual_data[key])})
            return rows, "columnar"
        return [manual_data], "single_dict"
    return [], "synthetic"


def _numeric_fields(rows: Sequence[Dict[str, Any]]) -> List[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        for key, value in row.items():
            if isinstance(value, (int, float)) or (isinstance(value, str) and re.search(r"-?\d", value)):
                counts[key] += 1
    return [key for key, _ in counts.most_common()]


def _label_field(rows: Sequence[Dict[str, Any]]) -> str:
    candidates = ("label", "name", "title", "segment", "category", "region", "team", "date", "period")
    for candidate in candidates:
        if any(candidate in row for row in rows):
            return candidate
    return next((key for row in rows for key, value in row.items() if isinstance(value, str)), "label")


def _stable_value(seed: int, index: int, base: float, spread: float = 0.18) -> float:
    wave = math.sin(seed * 0.01 + index * 0.83) * 0.5 + 0.5
    return round(base * (0.92 + wave * spread), 2)


def _synthetic_dashboard(domain: str, prompt: str, project_context: str, theme: str, audience: str, horizon: str):
    cfg = DOMAIN_LIBRARY.get(domain, DEFAULT_DOMAIN)
    seed = _seed(prompt, project_context, domain, horizon)
    primary = cfg["focus"].split(" and ")[0].title()
    kpis = [
        DashboardKPI(
            title="Primary Metric",
            value=_format_value(_stable_value(seed, 0, 1_240_000), "currency"),
            change=round(_delta(_stable_value(seed, 0, 1_240_000), _stable_value(seed, 1, 1_170_000)), 2),
            changeLabel=f"vs previous {horizon}",
            trend="up",
            format="currency",
            note=f"Headline signal for {primary.lower()}",
        ),
        DashboardKPI(
            title="Efficiency",
            value=_format_value(_stable_value(seed, 1, 68), "percent"),
            change=3.8,
            changeLabel="vs previous period",
            trend="up",
            format="percent",
            note="Operational efficiency",
        ),
        DashboardKPI(
            title="Supporting Metric",
            value=_format_value(_stable_value(seed, 2, 44_500), "compact"),
            change=5.1,
            changeLabel="vs previous period",
            trend="up",
            format="number",
            note="Secondary volume metric",
        ),
        DashboardKPI(
            title="Risk Metric",
            value=_format_value(_stable_value(seed, 3, 3.8), "percent"),
            change=-0.7,
            changeLabel="lower is better",
            trend="down",
            format="percent",
            note="Alerting metric",
        ),
    ]

    labels = [f"{horizon[:1].upper()}{idx + 1}" for idx in range(6)]
    charts = [
        DashboardChart(
            id="primary-trend",
            type="line",
            title="Primary Trend",
            subtitle="Generated from the request context",
            dataKeys=["primary", "benchmark"],
            data=[{"label": label, "primary": _stable_value(seed, idx, 900), "benchmark": _stable_value(seed, idx, 830)} for idx, label in enumerate(labels)],
            insight="The trend chart highlights the leading business metric over time.",
        ),
        DashboardChart(
            id="segment-bars",
            type="bar",
            title="Segment Comparison",
            subtitle="Top segments by value",
            dataKeys=["value", "share"],
            data=[{"label": name, "value": _stable_value(seed, idx, 210_000), "share": 12 + idx * 8} for idx, name in enumerate(["North", "South", "East", "West"])],
            insight="The bar chart helps compare operational buckets.",
        ),
        DashboardChart(
            id="mix-pie",
            type="pie",
            title="Mix Composition",
            subtitle="Contribution split",
            dataKeys=["value"],
            data=[{"name": name, "value": 18 + idx * 11 + (seed % 7)} for idx, name in enumerate(["Core", "Growth", "Expansion", "Retention"])],
            insight="The pie chart shows how the dashboard is distributed across core drivers.",
        ),
    ]

    tables = [
        DashboardTable(
            id="breakdown-table",
            title="Breakdown Table",
            subtitle="Compact decision view",
            columns=[
                DashboardTableColumn(key="label", label="Segment"),
                DashboardTableColumn(key="value", label="Primary Value"),
                DashboardTableColumn(key="share", label="Share"),
            ],
            data=charts[1].data,
        )
    ]
    thresholds = [
        DashboardThreshold(metric_name="Primary Metric", operator=">=", threshold_value=_stable_value(seed, 4, 1_000_000) * 0.92, severity="warning", message="Watch for any decline in the main KPI."),
        DashboardThreshold(metric_name="Risk Metric", operator="<=", threshold_value=5.0, severity="critical", message="Escalate when the risk metric rises above target."),
    ]
    summary = f"Designed a {horizon} {domain} dashboard with KPI cards, charts, a table, and thresholds for {cfg['focus']}."
    analysis = DashboardAnalysis(
        key_metrics=[item.title for item in kpis],
        key_signals=[f"{kpis[0].title} is the headline metric.", f"{len(charts)} charts give both trend and mix views."],
        chart_rationale=["Line charts show the main trend.", "Bar charts compare segments.", "Pie charts explain contribution mix."],
        threshold_rationale=["Thresholds sit just beyond the latest operating range to catch regressions early."],
        assumptions=["The dashboard is intended for executive or operational review.", "Prompt keywords determine the domain vocabulary."],
        confidence=0.84 if domain != "general" else 0.75,
    )
    schema = DashboardSchema(
        title=cfg["title"] if domain in DOMAIN_LIBRARY else DEFAULT_DOMAIN["title"],
        description=f"{cfg['title'] if domain in DOMAIN_LIBRARY else DEFAULT_DOMAIN['title']} generated from prompt context.",
        audience=audience,
        time_horizon=horizon,
        theme=theme,
        summary=summary,
        cards=kpis,
        kpis=kpis,
        charts=charts,
        tables=tables,
        thresholds=thresholds,
        highlights=[f"Domain inferred as {domain}.", f"Theme hint: {theme}.", f"Audience: {audience}."],
    )
    return schema, analysis, ["Validate metric names against your live schema.", "Connect thresholds to the alerting engine."]


def _manual_dashboard(rows: List[Dict[str, Any]], manual_data: Any, domain: str, prompt: str, project_context: str, theme: str, audience: str, horizon: str):
    cfg = DOMAIN_LIBRARY.get(domain, DEFAULT_DOMAIN)
    numeric = _numeric_fields(rows)
    label_key = _label_field(rows)
    kpis: List[DashboardKPI] = []
    thresholds: List[DashboardThreshold] = []
    if numeric:
        for idx, field in enumerate(numeric[:4]):
            current = float(rows[-1].get(field) or 0)
            previous = float(rows[-2].get(field) or current) if len(rows) > 1 else current
            fmt = "currency" if any(word in field.lower() for word in ("revenue", "cost", "expense", "value", "price")) else "percent" if any(word in field.lower() for word in ("rate", "margin", "churn", "retention", "ctr", "uptime")) else "number"
            kpis.append(DashboardKPI(title=field.replace("_", " ").title(), value=_format_value(current, fmt), change=round(_delta(current, previous), 2), changeLabel=f"vs previous {horizon}", trend="up" if current >= previous else "down", format=fmt, note="Derived from supplied data"))
            thresholds.append(DashboardThreshold(metric_name=field.replace("_", " ").title(), operator="<=" if fmt != "percent" else "<", threshold_value=round(current * 0.92, 2), severity="warning", message="Derived from manual data baseline."))
    if not kpis:
        return _synthetic_dashboard(domain, prompt, project_context, theme, audience, horizon)

    chart_field = numeric[0]
    chart_data = [{"label": str(row.get(label_key) or f"Row {idx + 1}"), "value": float(row.get(chart_field) or 0)} for idx, row in enumerate(rows[:8])]
    charts = [
        DashboardChart(id="manual-trend", type="line", title=f"{chart_field.replace('_', ' ').title()} Trend", subtitle="Built from supplied rows", dataKeys=["value"], data=chart_data, insight="This chart is driven directly by the uploaded or embedded dataset."),
    ]
    if len(numeric) > 1:
        charts.append(
            DashboardChart(id="manual-bars", type="bar", title="Metric Comparison", subtitle="Top two numeric fields", dataKeys=[_slug(numeric[0]), _slug(numeric[1])], data=[{"label": str(row.get(label_key) or f"Row {idx + 1}"), _slug(numeric[0]): float(row.get(numeric[0]) or 0), _slug(numeric[1]): float(row.get(numeric[1]) or 0)} for idx, row in enumerate(rows[:8])], insight="Use this to compare the strongest fields across rows."),
        )
    tables = [
        DashboardTable(
            id="manual-table",
            title="Structured Data Preview",
            subtitle="The records used to shape the dashboard",
            columns=[DashboardTableColumn(key=key, label=key.replace("_", " ").title()) for key in list(rows[0].keys())[:6]],
            data=rows[:8],
        )
    ]
    summary = f"Built a {horizon} {domain} dashboard from your structured data, with KPI cards, trend charts, and threshold rules."
    analysis = DashboardAnalysis(
        key_metrics=[item.title for item in kpis],
        key_signals=[f"{len(rows)} row(s) were detected.", f"{len(numeric)} numeric fields were used."],
        chart_rationale=["The leading numeric field becomes the trend line.", "Additional numeric fields are compared in a bar chart."],
        threshold_rationale=["Thresholds are placed just below the latest value to flag regressions."],
        assumptions=["The latest row represents the current state.", "Numeric fields with the most coverage are the best dashboard drivers."],
        confidence=0.88,
    )
    schema = DashboardSchema(
        title=cfg["title"],
        description=f"{cfg['title']} generated from the supplied dataset.",
        audience=audience,
        time_horizon=horizon,
        theme=theme,
        summary=summary,
        cards=kpis,
        kpis=kpis,
        charts=charts,
        tables=tables,
        thresholds=thresholds,
        highlights=[f"Detected {len(rows)} record(s).", f"Numeric fields: {', '.join(numeric[:4]) or 'none'}."],
    )
    return schema, analysis, ["Check the field labels before wiring live data.", "Connect the table rows to your renderer."]


async def _ask_gemini_json(prompt: str) -> Dict[str, Any] | None:
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        return None
    model = (
        os.getenv("GEMINI_MODEL_PRO")
        or os.getenv("GEMINI_MODEL")
        or os.getenv("GEMINI_MODEL_FLASH")
        or "gemini-2.5-pro"
    ).strip()
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(f"{endpoint}?key={api_key}", json=body)
            response.raise_for_status()
        payload = response.json()
        candidates = payload.get("candidates") or []
        if not candidates:
            return None
        parts = (((candidates[0] or {}).get("content") or {}).get("parts")) or []
        merged = "\n".join(str(part.get("text") or "").strip() for part in parts if isinstance(part, dict)).strip()
        if not merged:
            return None
        if merged.startswith("```"):
            merged = merged.strip("`")
            if merged.startswith("json"):
                merged = merged[4:].strip()
        parsed = json.loads(merged[merged.find("{") : merged.rfind("}") + 1])
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


async def build_dashboard_artifact(*, prompt: str, project_context: str, manual_data: Any, existing_dashboard: Dict[str, Any] | None, dashboard_theme: str | None, time_horizon: str | None, audience: str | None, context: Dict[str, Any]) -> DashboardDesignerArtifact:
    prompt_text = _clean(prompt)
    project_text = _clean(project_context)
    combined = "\n".join(part for part in (prompt_text, project_text) if part)
    domain = _infer_domain(combined or json.dumps(existing_dashboard or {}, ensure_ascii=True))
    horizon = _clean(time_horizon or context.get("timeHorizon")) or _infer_horizon(combined)
    theme = _clean(dashboard_theme or context.get("theme") or "dark-analytical")
    resolved_audience = _clean(audience or context.get("audience") or DOMAIN_LIBRARY.get(domain, DEFAULT_DOMAIN)["audience"])
    focus = _infer_focus(combined, DOMAIN_LIBRARY.get(domain, DEFAULT_DOMAIN)["focus"])
    resolved_manual = _find_manual_data(manual_data, context)
    rows, data_shape = _normalize_rows(resolved_manual)

    gemini_payload = None
    if (os.getenv("GEMINI_API_KEY") or "").strip():
        gemini_prompt = (
            "You generate compact dashboard schemas as JSON only.\n"
            "Return fields: title, description, summary, audience, time_horizon, theme, cards, kpis, charts, tables, thresholds, highlights, analysis, requestSummary, recommendedNextActions.\n"
            f"PROMPT: {prompt_text}\nPROJECT_CONTEXT: {project_text}\nDOMAIN: {domain}\nHORIZON: {horizon}\nAUDIENCE: {resolved_audience}\nMANUAL_DATA: {json.dumps(rows[:8] if rows else resolved_manual, ensure_ascii=True)}"
        )
        gemini_payload = await _ask_gemini_json(gemini_prompt)

    if gemini_payload:
        # We still normalize through the typed models so the response is reliable.
        try:
            cards = [DashboardKPI(**item) for item in gemini_payload.get("cards", gemini_payload.get("kpis", [])) if isinstance(item, dict)]
            charts = [DashboardChart(**item) for item in gemini_payload.get("charts", []) if isinstance(item, dict)]
            tables = [DashboardTable(**item) for item in gemini_payload.get("tables", []) if isinstance(item, dict)]
            thresholds = [DashboardThreshold(**item) for item in gemini_payload.get("thresholds", []) if isinstance(item, dict)]
            schema = DashboardSchema(
                title=_clean(gemini_payload.get("title")) or DOMAIN_LIBRARY.get(domain, DEFAULT_DOMAIN)["title"],
                description=_clean(gemini_payload.get("description")) or f"{domain.title()} dashboard generated from prompt context.",
                audience=_clean(gemini_payload.get("audience")) or resolved_audience,
                time_horizon=_clean(gemini_payload.get("time_horizon")) or horizon,
                theme=_clean(gemini_payload.get("theme")) or theme,
                summary=_clean(gemini_payload.get("summary")) or "Dashboard schema generated by Gemini.",
                cards=cards,
                kpis=cards,
                charts=charts,
                tables=tables,
                thresholds=thresholds,
                highlights=[_clean(item) for item in gemini_payload.get("highlights", []) if _clean(item)],
            )
            analysis = DashboardAnalysis(**(gemini_payload.get("analysis") or {}))
            request_summary = DashboardRequestSummary(
                prompt=prompt_text or project_text or "Dashboard request",
                project_context_present=bool(project_text),
                manual_data_present=resolved_manual is not None,
                domain=domain,
                audience=schema.audience,
                time_horizon=schema.time_horizon,
                focus=focus,
                data_shape=data_shape,
            )
            return DashboardDesignerArtifact(
                title=schema.title,
                subtitle=f"{schema.audience.title()} view for {schema.time_horizon} planning",
                summary=schema.summary,
                dashboardSchema=schema,
                requestSummary=request_summary,
                analysis=analysis,
                recommendedNextActions=[_clean(item) for item in gemini_payload.get("recommendedNextActions", []) if _clean(item)] or ["Connect the schema to your dashboard renderer."],
                sources=[{"type": "gemini", "model": os.getenv("GEMINI_MODEL_PRO") or os.getenv("GEMINI_MODEL") or "gemini-2.5-pro"}],
            )
        except Exception:
            pass

    if rows:
        schema, analysis, recommended = _manual_dashboard(rows, resolved_manual, domain, prompt_text, project_text, theme, resolved_audience, horizon)
    else:
        schema, analysis, recommended = _synthetic_dashboard(domain, prompt_text, project_text, theme, resolved_audience, horizon)

    request_summary = DashboardRequestSummary(
        prompt=prompt_text or project_text or "Dashboard request",
        project_context_present=bool(project_text),
        manual_data_present=resolved_manual is not None,
        domain=domain,
        audience=resolved_audience,
        time_horizon=horizon,
        focus=focus,
        data_shape=data_shape,
    )
    return DashboardDesignerArtifact(
        title=schema.title,
        subtitle=f"{resolved_audience.title()} view for {horizon} planning",
        summary=schema.summary,
        dashboardSchema=schema,
        requestSummary=request_summary,
        analysis=analysis,
        recommendedNextActions=recommended,
        sources=[
            {"type": "prompt", "present": bool(prompt_text)},
            {"type": "project_context", "present": bool(project_text)},
            {"type": "manual_data", "present": resolved_manual is not None, "shape": data_shape},
            {"type": "existing_dashboard", "present": bool(existing_dashboard)},
        ],
    )
