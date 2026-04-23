# Cyber AI SOC v2.0

AI-powered Security Operations Center with Windows Event Log integration, Bootstrap 5 Light UI, and modular FastAPI backend.

---

## Folder Structure

```
cyber-soc/
├── app/
│   ├── main.py                  # FastAPI app, router registration
│   ├── agent/
│   │   └── cyber_agent.py       # Threat detection engine (unchanged + bug fix)
│   ├── config/
│   │   └── settings.py          # Pydantic settings (env-driven)
│   ├── routes/
│   │   ├── analysis.py          # /analyze /history /health
│   │   └── logs.py              # /logs/realtime /logs/channels  ← NEW
│   ├── services/
│   │   ├── gemma.py             # Ollama LLM service
│   │   ├── virustotal.py        # VirusTotal enrichment
│   │   └── windows_logs.py      # Windows Event Log service ← NEW
│   └── utils/
│       ├── ioc_extractor.py     # IOC extraction
│       └── logger.py            # Logging config
├── frontend/
│   └── index.html               # Bootstrap 5 Light Theme dashboard ← NEW
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── run.py
```

---

## Quick Start

### 1. Clone & configure

```bash
cp .env.example .env
# Edit .env — add VIRUSTOTAL_API_KEY, confirm OLLAMA_* settings
```

### 2. Install dependencies

```bash
pip install -r requirements.txt

# Windows only — for real Event Log access:
pip install pywin32
```

### 3. Start Ollama (if using LLM analysis)

```bash
ollama run gemma4:31b-cloud
```

### 4. Run the app

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
# or:
python run.py
```

Open http://localhost:8000

---

## Docker

```bash
cp .env.example .env && nano .env
docker compose up --build
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | System health check |
| POST | `/analyze` | Analyze a log entry |
| GET | `/history` | Past analysis results |
| GET | `/logs/realtime?limit=10&channels=Security,System` | **NEW** Windows Event Logs |
| GET | `/logs/channels` | List available channels |
| GET | `/api/docs` | Swagger UI |

### `/logs/realtime` query params

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `limit` | int | 10 | 1–200 (hard cap enforced server-side) |
| `channels` | string | `Security,System,Application` | Comma-separated |

---

## Windows Log Integration

- **Windows host**: uses `pywin32` (`win32evtlog`) — install with `pip install pywin32`
- **Linux / Mac / Docker**: returns realistic simulated events for dev/testing
- Auto-detected at runtime — no config needed

---

## New Features (v2.0)

- `/logs/realtime` endpoint with `?limit=` and `?channels=` params
- Bootstrap 5 **Light Theme** dashboard (replaces dark theme)
- Sidebar navigation: Dashboard, Analyze, Windows Logs, History
- Dashboard shows latest AI analysis with full details and recommendations
- Windows Logs tab: Last 5 / Last 10 / Custom controls + channel checkboxes
- Auto-refresh toggle (every 10s) with live loader
- Event log table: timestamp, event ID, channel, source, severity, computer, message
- Stat cards with live dashboard metrics
- Confidence bar visualization
- IOC chip display grouped by type
- MITRE ATT&CK tag display
- Real-time navbar clock
- Mobile-responsive layout
- Bug fix: `ioc_list` NameError in `cyber_agent.py`
- Modular routes: `app/routes/analysis.py`, `app/routes/logs.py`
- Proper `logging` module throughout (replaces print statements)
- Input validation: custom limit clamped 1–200, channel whitelist enforced
