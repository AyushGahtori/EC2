# ALOS — Autonomous Logistics Operating System

ALOS is a plug-and-play, MongoDB-backed logistics agent. It runs a ReAct-style loop with real tools against a local data workspace you fill at install time.

## What's in the box

- **MongoDB-first runtime.** Everything lives in a local Mongo. External sources (CSV, Excel, REST API, remote Mongo) are *importers*, not the source of truth.
- **Onboarding wizard at `/onboarding`.** Pick a source, ALOS imports it, auto-builds an entity registry, and asks you for any missing fields just like a ReAct agent would.
- **Two providers.** Ollama and Groq, switchable per turn from the orchestrator UI. Paste a Groq key in the panel to use it without touching `.env`.
- **Real weather-aware routing.** `check_route_weather(origin, destination)` geocodes both endpoints with Open-Meteo, fetches a route from OSRM (great-circle fallback), samples waypoints, and returns a verdict — `go` / `caution` / `reroute`.
- **`ask_user` clarification tool.** Whenever the model is missing an arg, ambiguous, or unsure, it calls `ask_user` and a modal pops up asking the operator. The answer is fed back into the loop.

## Quick start

```bash
# 1. Install deps
npm install

# 2. Make sure MongoDB is reachable (local Mongo on default port works as-is)
#    Atlas/remote Mongo? Set MONGODB_URI in .env
cp .env.example .env

# 3. Start the app
npm run dev
```

Then open the app, go to **Onboarding**, and either:

1. **Upload CSV** files (one entity per file, header row defines fields).
2. **Upload an Excel workbook** (one entity per sheet).
3. **Pull from a REST API** (URL, optional auth header, optional JSON path).
4. **Clone a remote MongoDB** (URI + database name, optionally a list of collections).
5. **Start empty** with the built-in logistics schema.

When data is missing, the orchestrator will pop a clarification modal asking you to fill the gap. Anything you answer gets stored in the local Mongo so the agent never asks twice.

## Environment

Only Mongo is required. LLM keys are optional — Ollama works locally without one, Groq is enabled by either setting `GROQ_API_KEY` or pasting a key into the runtime panel.

| Variable | Purpose |
|---|---|
| `MONGODB_URI` | Mongo connection string. Defaults to `mongodb://localhost:27017`. |
| `MONGODB_DB` | Database name. Defaults to `alos`. |
| `ALOS_DEFAULT_PROVIDER` | `ollama` or `groq`. |
| `ALOS_DEFAULT_MODEL` | Model id. |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` / `OLLAMA_API_KEY` | Ollama settings. |
| `GROQ_BASE_URL` / `GROQ_MODEL` / `GROQ_API_KEY` | Groq settings. UI override available. |
| `ALOS_ENTITY_REGISTRY_JSON` | Optional pre-baked registry (skips the wizard). |

## Tools the agent can call

- `describe_workspace` — list entities and their fields.
- `summarize_entity` — totals, status mix, sample rows.
- `list_records` — filter / sort / project a collection.
- `create_record` — insert into a writable collection.
- `update_records` — update with required filters.
- `check_route_weather` — geocode + OSRM + Open-Meteo per-waypoint forecast, returns a verdict and reroute hint when severe.
- `ask_user` — pause and surface a clarification question to the operator.

## External services

- **MongoDB** — local-first, you control it.
- **Open-Meteo** for geocoding + weather (no API key).
- **OSRM public demo** for routing (with great-circle fallback if it's offline).
- **Ollama / Groq** for the LLM.

No data is sent to a third party except: place names → Open-Meteo, lat/lon pairs → OSRM and Open-Meteo, and chat messages → your chosen LLM provider.
