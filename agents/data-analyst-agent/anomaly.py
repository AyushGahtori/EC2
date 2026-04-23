from __future__ import annotations

import math
from statistics import mean, median
from typing import Any

def parse_numeric_series(raw_data: list[Any] | None, max_points: int) -> list[float]:
    if raw_data is None:
        raise ValueError("data is required.")
    if not isinstance(raw_data, list):
        raise ValueError("data must be an array of numbers.")
    if len(raw_data) == 0:
        raise ValueError("data must contain at least one numeric value.")
    if len(raw_data) > max_points:
        raise ValueError(f"data exceeds maximum supported size ({max_points}).")

    parsed: list[float] = []
    for value in raw_data:
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"data contains non-numeric value: {value!r}") from exc
        if math.isnan(numeric) or math.isinf(numeric):
            raise ValueError("data contains NaN/Infinity values which are not supported.")
        parsed.append(numeric)
    return parsed


def _zscore_detection(data: list[float], threshold: float = 3.0) -> list[int]:
    if len(data) < 2:
        return []
    avg = mean(data)
    variance = sum((x - avg) ** 2 for x in data) / len(data)
    std = math.sqrt(variance)
    if std <= 1e-12:
        return []
    flagged: list[int] = []
    for i, value in enumerate(data):
        z = abs((value - avg) / std)
        if z > threshold:
            flagged.append(i)
    return flagged


def _percentile(sorted_data: list[float], pct: float) -> float:
    if not sorted_data:
        return 0.0
    k = (len(sorted_data) - 1) * pct
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_data[int(k)])
    return float(sorted_data[f] + (sorted_data[c] - sorted_data[f]) * (k - f))


def _isolation_detection(data: list[float]) -> list[int]:
    if len(data) < 4:
        return []
    # Lightweight robust detector that approximates isolation behavior
    # using median absolute deviation (MAD) and IQR fences.
    sorted_vals = sorted(data)
    med = median(sorted_vals)
    deviations = [abs(v - med) for v in data]
    mad = median(deviations)

    q1 = _percentile(sorted_vals, 0.25)
    q3 = _percentile(sorted_vals, 0.75)
    iqr = q3 - q1
    lower_fence = q1 - 1.5 * iqr
    upper_fence = q3 + 1.5 * iqr

    flagged: list[int] = []
    for i, value in enumerate(data):
        mad_score = abs(value - med) / max(mad * 1.4826, 1e-9)
        if value < lower_fence or value > upper_fence or mad_score > 3.5:
            flagged.append(i)
    return flagged


def _series_stats(data: list[float]) -> dict[str, float | int]:
    if not data:
        return {
            "count": 0,
            "min": 0.0,
            "max": 0.0,
            "mean": 0.0,
            "median": 0.0,
            "std": 0.0,
            "iqr": 0.0,
        }
    sorted_vals = sorted(data)
    q1 = _percentile(sorted_vals, 0.25)
    q3 = _percentile(sorted_vals, 0.75)
    avg = mean(data)
    variance = sum((x - avg) ** 2 for x in data) / len(data)
    std = math.sqrt(variance)
    return {
        "count": int(len(data)),
        "min": float(sorted_vals[0]),
        "max": float(sorted_vals[-1]),
        "mean": float(avg),
        "median": float(median(sorted_vals)),
        "std": float(std),
        "iqr": float(q3 - q1),
    }


def detect_anomalies(data: list[float], label: str = "dataset") -> dict[str, Any]:
    if len(data) < 2:
        return {
            "status": "ok",
            "indices": [],
            "flaggedValues": [],
            "message": "Insufficient data for anomaly detection. Provide at least two values.",
            "zscoreIndices": [],
            "isolationIndices": [],
            "confidence": "low",
            "stats": _series_stats(data),
        }

    zscore_indices = _zscore_detection(data)
    isolation_indices = _isolation_detection(data)
    merged = sorted(set(zscore_indices) | set(isolation_indices))
    flagged = [data[index] for index in merged]
    status = "anomaly" if merged else "ok"

    overlap = set(zscore_indices) & set(isolation_indices)
    if status == "ok":
        confidence = "high"
    elif overlap:
        confidence = "high"
    elif len(merged) == 1:
        confidence = "medium"
    else:
        confidence = "low"

    if status == "anomaly":
        message = (
            f"Potential anomaly detected in {label}. "
            f"Flagged indices: {merged}. Values: {flagged}."
        )
    else:
        message = f"No anomaly detected in {label}. Values are within expected bounds."

    return {
        "status": status,
        "indices": merged,
        "flaggedValues": flagged,
        "message": message,
        "zscoreIndices": zscore_indices,
        "isolationIndices": isolation_indices,
        "confidence": confidence,
        "stats": _series_stats(data),
    }
