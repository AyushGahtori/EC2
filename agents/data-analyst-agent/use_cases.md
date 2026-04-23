# Data Analyst Agent - 10 Validation Use Cases

1. `monitor` with clear outlier
- Input: `[100, 102, 98, 105, 5000, 101, 99]`
- Expectation: `status=success`, `anomaly.status=anomaly`, flagged index includes `4`.

2. `monitor` with stable data
- Input: `[99, 100, 101, 98, 100, 102]`
- Expectation: `status=success`, anomaly may be `ok`, no hard failure.

3. `monitor` called twice with same payload
- Input: same as case #1 and same `userId`.
- Expectation: second response has `result.cache.hit=true` (memory/firestore).

4. `monitor` with non-numeric value
- Input: `[10, "abc", 12]`
- Expectation: `status=needs_input` with a clear payload correction message.

5. `monitor` with oversized dataset
- Input: more than `DATA_ANALYST_DATASET_MAX_POINTS`.
- Expectation: `status=needs_input` and no crash.

6. `autonomous` with goal only
- Input: goal/prompt without `data`.
- Expectation: `status=success`, strategic guidance + `nextInputs`.

7. `autonomous` with goal and data
- Input: goal + numeric data.
- Expectation: `status=success`, includes anomaly block and recommendations.

8. `autonomous` missing goal and prompt
- Input: no goal/prompt.
- Expectation: `status=needs_input`, suggests `goal or prompt`.

9. `list_capabilities`
- Input: action only.
- Expectation: `status=success`, returns supported actions/aliases.

10. SSE streaming monitor
- Endpoint: `/dataanalyst/monitor/stream`.
- Expectation: emits `status`, at least one `token`, then `done`.

## Why Caching Is Enabled

- Repeated monitor/autonomous requests occur frequently in conversational loops.
- Anomaly scoring + LLM summarization are deterministic for identical inputs, so TTL cache saves latency and API spend.
- Cache can be bypassed using `forceRefresh=true` when fresh recalculation is required.
