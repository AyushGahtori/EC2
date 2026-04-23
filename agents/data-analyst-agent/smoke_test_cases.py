from __future__ import annotations

import os
import sys

os.environ.setdefault("DATA_ANALYST_LLM_PROVIDER", "disabled")

from fastapi.testclient import TestClient

from server import app


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run() -> int:
    client = TestClient(app)
    checks: list[str] = []

    # 1) Health route
    resp = client.get("/dataanalyst/health")
    assert_true(resp.status_code == 200, "health endpoint failed")
    assert_true(resp.json().get("status") == "healthy", "health payload mismatch")
    checks.append("health")

    # 2) monitor with outlier
    monitor_payload = {
        "taskId": "smoke-1",
        "userId": "smoke-user",
        "agentId": "data-analyst-agent",
        "action": "monitor",
        "label": "revenue",
        "data": [100, 102, 98, 105, 5000, 101, 99],
    }
    resp = client.post("/dataanalyst/action", json=monitor_payload)
    body = resp.json()
    assert_true(resp.status_code == 200, "monitor action status code mismatch")
    assert_true(body.get("status") == "success", "monitor action should succeed")
    assert_true(body.get("result", {}).get("anomaly", {}).get("status") == "anomaly", "monitor should detect anomaly")
    checks.append("monitor_anomaly")

    # 3) monitor with stable data
    stable_payload = {
        **monitor_payload,
        "taskId": "smoke-2",
        "data": [99, 100, 101, 98, 100, 102],
    }
    resp = client.post("/dataanalyst/action", json=stable_payload)
    body = resp.json()
    assert_true(body.get("status") in {"success", "partial_success"}, "stable monitor should complete")
    checks.append("monitor_stable")

    # 4) cache hit on repeated monitor
    resp = client.post("/dataanalyst/action", json=monitor_payload)
    cache_meta = resp.json().get("result", {}).get("cache", {})
    assert_true(cache_meta.get("hit") is True, "expected cache hit for repeated monitor payload")
    checks.append("cache_hit")

    # 5) invalid payload
    invalid_payload = {
        **monitor_payload,
        "taskId": "smoke-3",
        "data": [10, "oops", 12],
    }
    resp = client.post("/dataanalyst/action", json=invalid_payload)
    body = resp.json()
    assert_true(body.get("status") == "needs_input", "invalid payload should return needs_input")
    checks.append("invalid_data")

    # 6) autonomous without data
    autonomous_payload = {
        "taskId": "smoke-4",
        "userId": "smoke-user",
        "agentId": "data-analyst-agent",
        "action": "autonomous",
        "goal": "Review monthly churn stability and suggest follow-ups",
    }
    resp = client.post("/dataanalyst/action", json=autonomous_payload)
    body = resp.json()
    assert_true(body.get("status") == "success", "autonomous without data should succeed")
    checks.append("autonomous_no_data")

    # 7) autonomous with data
    autonomous_data_payload = {
        **autonomous_payload,
        "taskId": "smoke-5",
        "data": [12, 14, 13, 12, 77, 11],
        "label": "active_users",
    }
    resp = client.post("/dataanalyst/action", json=autonomous_data_payload)
    body = resp.json()
    assert_true(body.get("status") in {"success", "partial_success"}, "autonomous with data should complete")
    assert_true("anomaly" in (body.get("result") or {}), "autonomous with data should include anomaly block")
    checks.append("autonomous_with_data")

    # 8) missing goal
    missing_goal_payload = {
        "taskId": "smoke-6",
        "userId": "smoke-user",
        "agentId": "data-analyst-agent",
        "action": "autonomous",
    }
    resp = client.post("/dataanalyst/action", json=missing_goal_payload)
    body = resp.json()
    assert_true(body.get("status") == "needs_input", "missing goal should request input")
    checks.append("missing_goal")

    # 9) list capabilities
    resp = client.post(
        "/dataanalyst/action",
        json={
            "taskId": "smoke-7",
            "userId": "smoke-user",
            "agentId": "data-analyst-agent",
            "action": "list_capabilities",
        },
    )
    body = resp.json()
    assert_true(body.get("status") == "success", "capabilities should succeed")
    assert_true("actions" in (body.get("result") or {}), "capabilities payload missing actions")
    checks.append("capabilities")

    # 10) SSE stream contract
    with client.stream(
        "POST",
        "/dataanalyst/monitor/stream",
        json={"data": [100, 102, 98, 105, 5000, 101, 99], "label": "revenue"},
    ) as stream_resp:
        assert_true(stream_resp.status_code == 200, "SSE route status mismatch")
        lines = [line for line in stream_resp.iter_lines() if line]
    merged = "\n".join(lines)
    assert_true("event: status" in merged, "SSE missing status event")
    assert_true("event: done" in merged, "SSE missing done event")
    checks.append("stream")

    print("Smoke cases passed:", ", ".join(checks))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except AssertionError as exc:
        print(f"Smoke cases failed: {exc}")
        raise SystemExit(1)
    except Exception as exc:
        print(f"Smoke test execution failed: {exc}")
        raise SystemExit(1)
