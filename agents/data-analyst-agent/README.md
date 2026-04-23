# Data Analyst Agent (EC2)

EC2-native anomaly and data quality agent for AI Everyone.

## Routes

- `GET /health`
- `GET /dataanalyst/health`
- `POST /dataanalyst/action`
- `POST /dataanalyst/monitor`
- `POST /dataanalyst/autonomous`
- `POST /dataanalyst/monitor/stream`

## Supported Actions

- `monitor`
- `autonomous`
- `list_capabilities`

Action aliases are normalized in `service.py`.

## Features

- Z-score + Isolation Forest anomaly detection
- Goal-driven autonomous analysis mode
- SSE streaming monitor endpoint
- Friendly, structured failures (`needs_input` vs `failed`)
- TTL cache (memory) with optional Firestore cache/logging

## Local Validation

```bash
uv run --python 3.12 --with-requirements requirements.txt smoke_test_cases.py
```
