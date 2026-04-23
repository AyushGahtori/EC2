from __future__ import annotations

import os

os.environ.setdefault("VIRUSTOTAL_API_KEY", "")
os.environ.setdefault("OLLAMA_TIMEOUT", "3")

from fastapi.testclient import TestClient

from server import app


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run() -> int:
    client = TestClient(app)
    checks: list[str] = []

    # 1) health route
    resp = client.get("/cybersoc/health")
    assert_true(resp.status_code == 200, "cybersoc health should return 200")
    assert_true(resp.json().get("status") == "healthy", "health payload mismatch")
    checks.append("health")

    # 2) capabilities
    resp = client.get("/cybersoc/capabilities")
    body = resp.json()
    assert_true(resp.status_code == 200, "capabilities endpoint should return 200")
    assert_true(body.get("status") == "success", "capabilities should return success")
    assert_true("actions" in (body.get("result") or {}), "capabilities should include actions")
    checks.append("capabilities")

    # 3) analyze simple brute-force log
    analyze_payload = {
        "taskId": "soc-1",
        "userId": "smoke-user",
        "agentId": "cyber-soc-agent",
        "action": "analyze_log",
        "log": (
            "2026-04-14 10:17:45 WARN Failed login attempt user admin ip 192.168.1.11\n"
            "2026-04-14 10:17:49 WARN Failed password for admin from 192.168.1.11\n"
            "2026-04-14 10:17:53 WARN Authentication failure from 192.168.1.11"
        ),
    }
    resp = client.post("/cybersoc/action", json=analyze_payload)
    body = resp.json()
    assert_true(resp.status_code == 200, "analyze action status mismatch")
    assert_true(body.get("status") in {"success", "partial_success"}, "analyze should succeed")
    assert_true(body.get("type") == "cyber_soc_analysis_result", "analyze type mismatch")
    checks.append("analyze_simple")

    # 4) empty input -> needs_input
    empty_payload = {"action": "analyze_log", "log": "  "}
    resp = client.post("/cybersoc/action", json=empty_payload)
    body = resp.json()
    assert_true(body.get("status") == "needs_input", "empty log should ask for input")
    checks.append("empty_input")

    # 5) windows logs fetch with custom channels/limit
    windows_payload = {"action": "fetch_windows_logs", "channels": ["Security", "System"], "limit": 5}
    resp = client.post("/cybersoc/action", json=windows_payload)
    body = resp.json()
    result = body.get("result") or {}
    assert_true(body.get("status") == "success", "windows logs fetch should succeed")
    assert_true(int(result.get("count", 0)) <= 5, "windows logs should respect limit")
    assert_true(isinstance(result.get("logs"), list), "windows logs payload should include logs array")
    checks.append("windows_fetch")

    # 6) analyze windows logs (multi-step flow)
    resp = client.post("/cybersoc/action", json={"action": "analyze_windows_logs", "limit": 10})
    body = resp.json()
    result = body.get("result") or {}
    assert_true(body.get("status") in {"success", "partial_success"}, "windows analyze should complete")
    assert_true("analysis" in result and "windowsLogs" in result, "windows analyze payload incomplete")
    checks.append("windows_analyze")

    # 7) history should include prior analyses
    resp = client.post("/cybersoc/action", json={"action": "get_history"})
    body = resp.json()
    result = body.get("result") or {}
    assert_true(body.get("status") == "success", "history should succeed")
    assert_true(int(result.get("count", 0)) >= 2, "history should include previous analyses")
    checks.append("history")

    # 8) cache behavior for repeated analyze
    resp = client.post("/cybersoc/action", json=analyze_payload)
    body = resp.json()
    cache_meta = (body.get("result") or {}).get("cache") or {}
    assert_true(cache_meta.get("hit") is True, "second analyze should be cache hit")
    checks.append("cache_hit")

    # 9) force refresh bypasses cache
    refresh_payload = dict(analyze_payload)
    refresh_payload["forceRefresh"] = True
    resp = client.post("/cybersoc/action", json=refresh_payload)
    body = resp.json()
    cache_meta = (body.get("result") or {}).get("cache") or {}
    assert_true(cache_meta.get("hit") is False, "forceRefresh should bypass cache")
    checks.append("cache_force_refresh")

    # 10) dashboard + failure action path
    resp = client.post("/cybersoc/action", json={"action": "dashboard_overview"})
    body = resp.json()
    assert_true(body.get("status") == "success", "dashboard should succeed")
    stats = ((body.get("result") or {}).get("stats") or {})
    assert_true(int(stats.get("totalAnalyses", 0)) >= 2, "dashboard stats should reflect analyses")

    resp = client.post("/cybersoc/action", json={"action": "unsupported_foo"})
    fail_body = resp.json()
    assert_true(fail_body.get("status") == "failed", "unsupported action should fail safely")
    checks.append("dashboard_and_failure")

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

