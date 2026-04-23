# Cyber SOC Agent - 10 Validation Use Cases

1. Simple brute-force detection  
- Prompt/action: `analyze_log` with repeated failed logins from one IP.  
- Expectation: `status=success`, threat/risk elevated, recommendations returned.

2. Complex multi-stage attack chain  
- Prompt/action: `analyze_log` with encoded PowerShell + privilege escalation + outbound callback.  
- Expectation: multi-stage indicators, high/critical severity, MITRE mapping present.

3. Failure-safe action handling  
- Prompt/action: unknown action (for example `unsupported_foo`).  
- Expectation: `status=failed` with safe user-facing guidance, no stack trace.

4. Empty input handling  
- Prompt/action: `analyze_log` with empty/whitespace log text.  
- Expectation: `status=needs_input` with correction guidance.

5. Windows log fetch (channel constrained)  
- Prompt/action: `fetch_windows_logs` with `channels=["Security","System"]`, `limit=5`.  
- Expectation: bounded result count, proper channel filtering.

6. Windows fetch -> analyze multi-step flow  
- Prompt/action: `analyze_windows_logs` with `limit=10`.  
- Expectation: fetch + synthesized log text + analysis in one response.

7. Dashboard/overview state  
- Prompt/action: `dashboard_overview` after multiple analyses.  
- Expectation: stat cards (`total/high/low/avgConfidence`) + latest analysis + recent activity.

8. History continuity  
- Prompt/action: `get_history` after prior actions.  
- Expectation: analysis records persisted in in-memory history with count and metadata.

9. Caching-heavy repeat analyze  
- Prompt/action: same `analyze_log` request twice.  
- Expectation: second response `result.cache.hit=true`.

10. Retry/recovery with cache bypass  
- Prompt/action: repeated `analyze_log` with `forceRefresh=true`.  
- Expectation: fresh recomputation (`result.cache.hit=false`) while preserving response contract.

## Caching Design

- Analyze cache (`CYBER_SOC_ANALYZE_CACHE_TTL_SECONDS`) reduces duplicate LLM+VT latency.
- Windows log fetch cache (`CYBER_SOC_WINDOWS_LOG_CACHE_TTL_SECONDS`) stabilizes rapid refresh calls.
- Dashboard cache (`CYBER_SOC_DASHBOARD_CACHE_TTL_SECONDS`) avoids redundant aggregation churn.
- `forceRefresh=true` bypasses cache for recovery/retry scenarios.

## Security Guardrails

- No secrets hardcoded in code or `.env.example`.
- Action wrapper never returns raw stack traces to users.
- Unknown/invalid actions return safe structured failures.
