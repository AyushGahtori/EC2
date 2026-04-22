"""Analytics helpers for financial snapshots, trends, and categories."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class PeriodSummary:
    symbol: str
    label: str
    month: int
    year: int
    revenue: float
    expenses: float
    profit: float
    margin: float
    gross_profit: float
    supplier_cost: float
    categories: dict[str, dict[str, float]]


def _extract_metrics(statement: dict[str, Any]) -> dict[str, float]:
    revenue = float(statement.get("revenue") or 0.0)
    expenses = float(statement.get("operatingExpenses") or 0.0)
    net_income = float(statement.get("netIncome") or 0.0)
    cogs = float(statement.get("costOfRevenue") or 0.0)
    gross_profit = float(statement.get("grossProfit") or (revenue - cogs))
    rnd = float(statement.get("researchAndDevelopmentExpenses") or 0.0)
    marketing = float(statement.get("sellingAndMarketingExpenses") or 0.0)
    admin = float(statement.get("generalAndAdministrativeExpenses") or 0.0)
    margin = round((net_income / revenue) * 100, 2) if revenue > 0 else 0.0
    return {
        "revenue": revenue,
        "expenses": expenses,
        "profit": net_income,
        "margin": margin,
        "cogs": cogs,
        "gross_profit": gross_profit,
        "rnd": rnd,
        "marketing": marketing,
        "admin": admin,
        "supplier_cost": round(expenses * 0.35, 2),
    }


def _parse_period(statement: dict[str, Any]) -> tuple[int, int]:
    date_raw = str(statement.get("date") or "")
    if date_raw:
        try:
            dt = datetime.fromisoformat(date_raw)
            return dt.month, dt.year
        except ValueError:
            pass
    year = int(statement.get("calendarYear") or datetime.utcnow().year)
    return 12, year


def statement_to_summary(symbol: str, statement: dict[str, Any], label_prefix: str = "FY") -> PeriodSummary:
    metrics = _extract_metrics(statement)
    month, year = _parse_period(statement)
    label = f"{label_prefix} {year}"
    categories = {
        "cogs": {"label": "COGS", "expenses": metrics["cogs"], "supplier_cost": metrics["cogs"]},
        "rnd": {"label": "R&D", "expenses": metrics["rnd"], "supplier_cost": 0.0},
        "marketing": {"label": "Marketing", "expenses": metrics["marketing"], "supplier_cost": 0.0},
        "admin": {"label": "Admin", "expenses": metrics["admin"], "supplier_cost": 0.0},
    }
    return PeriodSummary(
        symbol=symbol.upper(),
        label=label,
        month=month,
        year=year,
        revenue=metrics["revenue"],
        expenses=metrics["expenses"],
        profit=metrics["profit"],
        margin=metrics["margin"],
        gross_profit=metrics["gross_profit"],
        supplier_cost=metrics["supplier_cost"],
        categories=categories,
    )


def summarize_dashboard(symbol: str, statements: list[dict[str, Any]]) -> dict[str, Any]:
    current = statement_to_summary(symbol, statements[0])
    previous = statement_to_summary(symbol, statements[1]) if len(statements) > 1 else None

    return {
        "symbol": current.symbol,
        "periodLabel": current.label,
        "summary": {
            "revenue": current.revenue,
            "expenses": current.expenses,
            "profit": current.profit,
            "margin": current.margin,
            "grossProfit": current.gross_profit,
            "supplierCost": current.supplier_cost,
        },
        "comparison": {
            "revenueChangePct": pct_change(current.revenue, previous.revenue) if previous else None,
            "expenseChangePct": pct_change(current.expenses, previous.expenses) if previous else None,
            "marginChangePct": round(current.margin - previous.margin, 2) if previous else None,
            "supplierChangePct": pct_change(current.supplier_cost, previous.supplier_cost) if previous else None,
        },
        "anomalies": detect_anomalies(current, previous),
    }


def summarize_categories(symbol: str, statements: list[dict[str, Any]], month: int | None = None) -> dict[str, Any]:
    current = statement_to_summary(symbol, statements[0])
    title = calendar.month_abbr[month] if isinstance(month, int) and 1 <= month <= 12 else current.label
    return {
        "symbol": current.symbol,
        "periodLabel": title,
        "categories": current.categories,
    }


def summarize_trends(symbol: str, statements: list[dict[str, Any]], periods: int = 6) -> dict[str, Any]:
    sliced = statements[: max(2, min(periods, len(statements)))]
    rows = [statement_to_summary(symbol, statement) for statement in reversed(sliced)]
    trend_rows = [
        {
            "label": row.label,
            "revenue": row.revenue,
            "expenses": row.expenses,
            "profit": row.profit,
            "margin": row.margin,
        }
        for row in rows
    ]
    return {"symbol": symbol.upper(), "trend": trend_rows, "forecast": build_forecast(trend_rows)}


def detect_anomalies(current: PeriodSummary, previous: PeriodSummary | None) -> list[str]:
    if previous is None:
        return []
    anomalies: list[str] = []
    expense_delta = pct_change(current.expenses, previous.expenses)
    if expense_delta is not None and expense_delta > 20:
        anomalies.append(f"Expense spike detected: +{expense_delta:.1f}% vs previous period.")
    margin_drop = previous.margin - current.margin
    if margin_drop > 5:
        anomalies.append(f"Margin erosion detected: -{margin_drop:.1f}%")
    supplier_delta = pct_change(current.supplier_cost, previous.supplier_cost)
    if supplier_delta is not None and supplier_delta > 15:
        anomalies.append(f"Supplier-linked cost increased by +{supplier_delta:.1f}%")
    if current.profit < 0:
        anomalies.append(f"Net loss detected ({abs(current.profit):,.0f}).")
    return anomalies


def pct_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return round(((current - previous) / abs(previous)) * 100, 2)


def build_forecast(trend_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(trend_rows) < 2:
        return None
    prev = trend_rows[-2]
    latest = trend_rows[-1]
    prev_revenue = float(prev["revenue"] or 0.0)
    prev_expenses = float(prev["expenses"] or 0.0)
    latest_revenue = float(latest["revenue"] or 0.0)
    latest_expenses = float(latest["expenses"] or 0.0)
    revenue_growth = ((latest_revenue - prev_revenue) / prev_revenue) if prev_revenue else 0.0
    expense_growth = ((latest_expenses - prev_expenses) / prev_expenses) if prev_expenses else revenue_growth
    forecast_revenue = max(latest_revenue * (1 + revenue_growth), 0.0)
    forecast_expenses = max(latest_expenses * (1 + (expense_growth * 0.8)), 0.0)
    forecast_profit = forecast_revenue - forecast_expenses
    forecast_margin = (forecast_profit / forecast_revenue * 100) if forecast_revenue > 0 else 0.0
    return {
        "revenue": round(forecast_revenue, 2),
        "expenses": round(forecast_expenses, 2),
        "profit": round(forecast_profit, 2),
        "margin": round(forecast_margin, 2),
        "assumption": "Trend-based forecast using the latest reported periods with moderated expense growth.",
    }
