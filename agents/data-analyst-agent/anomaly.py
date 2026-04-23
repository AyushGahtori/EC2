from __future__ import annotations

import math
from statistics import mean, median
from typing import Any

import numpy as np
from sklearn.ensemble import IsolationForest


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
    arr = np.asarray(data, dtype=float)
    std = float(arr.std())
    if std <= 1e-12:
        return []
    z_scores = np.abs((arr - arr.mean()) / std)
    return [int(i) for i in np.where(z_scores > threshold)[0]]


def _isolation_detection(data: list[float]) -> list[int]:
    if len(data) < 4:
        return []
    arr = np.asarray(data, dtype=float).reshape(-1, 1)
    contamination = min(0.20, max(1.0 / len(data), 0.02))
    contamination = min(contamination, 0.49)
    clf = IsolationForest(
        contamination=contamination,
        random_state=42,
        n_estimators=120,
    )
    preds = clf.fit_predict(arr)  # -1 => outlier
    return [int(i) for i, pred in enumerate(preds) if pred == -1]


def _series_stats(data: list[float]) -> dict[str, float | int]:
    arr = np.asarray(data, dtype=float)
    q1 = float(np.percentile(arr, 25))
    q3 = float(np.percentile(arr, 75))
    return {
        "count": int(arr.size),
        "min": float(arr.min()),
        "max": float(arr.max()),
        "mean": float(mean(data)),
        "median": float(median(data)),
        "std": float(arr.std()),
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
