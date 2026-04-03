"""FinancialModelingPrep API client for strata-agent."""

from __future__ import annotations

import os
from typing import Any

import httpx

FMP_BASE_URL = os.getenv(
    "FMP_BASE_URL",
    "https://financialmodelingprep.com/stable/income-statement",
)


def _fmp_api_key() -> str:
    return (os.getenv("FMP_API_KEY") or os.getenv("STRATA_FMP_API_KEY") or "").strip()


def _normalize_symbol(symbol: str | None) -> str:
    default_symbol = (os.getenv("STRATA_DEFAULT_SYMBOL") or "AAPL").strip()
    return (symbol or default_symbol).upper()


async def fetch_income_statements(symbol: str | None, period: str | None = None) -> list[dict[str, Any]]:
    api_key = _fmp_api_key()
    if not api_key:
        raise ValueError("Missing financial API key. Set FMP_API_KEY.")

    normalized_symbol = _normalize_symbol(symbol)
    params: dict[str, str] = {"symbol": normalized_symbol, "apikey": api_key}
    if period:
        params["period"] = period

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.get(FMP_BASE_URL, params=params)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            raise ValueError("Financial API rate limit reached. Please retry shortly.") from exc
        raise ValueError(f"Financial API returned HTTP {exc.response.status_code}.") from exc
    except httpx.HTTPError as exc:
        raise ValueError("Could not reach the financial API endpoint.") from exc

    data = response.json()
    if not isinstance(data, list) or len(data) == 0:
        raise ValueError(f"No financial data available for {normalized_symbol}.")
    return data
