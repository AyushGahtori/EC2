# Shelfie Grocery Agent (EC2 Runtime)

Detached EC2 agent for conversational grocery planning with session memory.

## Endpoints

- `GET /health`
- `GET /shelfie/health`
- `POST /shelfie/action`
- `POST /shelfie-grocery/action`

## Supported Actions

- `run_shelfie_grocery_agent`
- `get_history`
- `list_sessions`
- `reset_session`
- `list_capabilities`

## Runtime Notes

- Primary session cache: Redis (`REDIS_URL`) with automatic in-memory fallback.
- Primary history store: MongoDB (`MONGODB_URL`) with automatic file fallback.
- LLM providers preserved from source: `ollama`, `ollama_cloud`, `openai`, `anthropic`, `gemini`.
- Existing chat API from source remains available under `/api/v1/*`.
