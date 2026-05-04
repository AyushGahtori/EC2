# MAIA Developer Guide

## Table of Contents
1. [How the Agent Works](#how-the-agent-works)
2. [Data Flow](#data-flow)
3. [Adding New Tools](#adding-new-tools)
4. [Adding New LLM Providers](#adding-new-llm-providers)
5. [State Management](#state-management)
6. [SSE Streaming Architecture](#sse-streaming-architecture)
7. [MongoDB Schema](#mongodb-schema)
8. [Redis Caching Strategy](#redis-caching-strategy)
9. [Common Issues](#common-issues)

---

## How the Agent Works

The agent is a **LangGraph StateGraph** implementing the ReAct pattern.
Every user message enters the graph at the `agent` node. The LLM decides what to do next.

```
User Message
     │
     ▼
┌────────────┐    has tool_calls?    ┌────────────┐
│   agent    │──────── yes ─────────▶│   tools    │
│  (LLM +    │                       │ (ToolNode) │
│   tools)   │◀──── always ──────────│            │
└────────────┘                       └────────────┘
     │
     │ no tool_calls (final answer)
     ▼
    END
```

The LLM is bound to all 8 tools via `llm.bind_tools(ALL_TOOLS)`. When it decides
to call a tool, LangGraph automatically routes to `ToolNode`, executes it, and
feeds the result back into the agent as a `ToolMessage`. This loop repeats until
the LLM produces a response with no tool calls.

**Key insight:** The LLM reads the system prompt (which explains the ReAct cycle)
and the available tools, then makes ALL decisions autonomously. There is no
`if "poster" in message` logic anywhere.

---

## Data Flow

### Full request lifecycle:

```
1. User types message + optional image in Next.js UI

2. Zustand store calls sendMessage()
   └─▶ Opens EventSource to GET /api/chat/stream/{session_id}?message=...

3. FastAPI chat.py receives request:
   ├─ Loads message history from MongoDB
   ├─ Loads product context from Redis (or MongoDB)
   ├─ Loads brand guidelines from session document
   ├─ Saves user message to MongoDB
   └─ Starts SSE response stream

4. run_agent_streaming() is called in marketing_agent.py
   ├─ Builds LangChain message list with full history
   ├─ Creates initial AgentState
   └─ Calls graph.astream_events(initial_state, version="v2")

5. LangGraph streams events:
   ├─ on_chat_model_stream → token events → frontend appends to message
   ├─ on_tool_start        → phase/tool_call events → AgentActivityPanel
   ├─ on_tool_end          → tool_result events + content events
   └─ (loop until done)

6. FastAPI SSE handler translates events to JSON and sends via EventSource

7. Frontend EventSource receives events:
   ├─ "token"       → appends to streaming message bubble
   ├─ "phase"       → updates AgentActivityPanel
   ├─ "tool_call"   → adds tool chip to activity panel
   ├─ "tool_result" → marks tool chip success/fail
   ├─ "content"     → renders ContentPreview (poster/hashtags/social/etc)
   └─ "done"        → closes EventSource, marks message as complete

8. After stream ends, assistant response is saved to MongoDB
```

---

## Adding New Tools

1. Open `backend/agents/tools.py`
2. Define your tool using the `@tool` decorator:

```python
@tool
def generate_email_campaign(
    product_analysis: str,
    email_type: str = "launch",
    tone: str = "professional",
) -> str:
    """
    Generate a marketing email campaign for a product.

    Args:
        product_analysis: JSON string from analyze_product_image tool
        email_type: launch | promotional | newsletter | re-engagement
        tone: professional | friendly | urgent | luxury

    Returns:
        Complete email with subject line, preview text, and body
    """
    system = """You are an email marketing expert..."""
    user = f"""Product: {product_analysis}\nType: {email_type}\nTone: {tone}"""
    return _sync_llm(system, user)
```

3. Add it to `ALL_TOOLS`:
```python
ALL_TOOLS = [
    analyze_product_image,
    generate_marketing_copy,
    # ... existing tools ...
    generate_email_campaign,   # ← add here
]
```

4. The LLM will automatically discover it from its docstring and schema.
   No other changes needed — the agent will use it when appropriate.

**Tips for writing good tools:**
- Write a clear, descriptive docstring — the LLM reads this to decide when to use the tool
- Use typed parameters — LangChain generates the JSON schema from these
- Keep tools focused and single-purpose
- Always handle exceptions and return meaningful error messages

---

## Adding New LLM Providers

1. Open `backend/services/llm_service.py`
2. Add a new builder function:

```python
def _build_mistral_llm() -> BaseChatModel:
    from langchain_mistralai import ChatMistralAI
    settings = get_settings()
    return ChatMistralAI(
        model=settings.mistral_model,
        api_key=settings.mistral_api_key,
        temperature=settings.agent_temperature,
    )
```

3. Register it in `get_llm()`:
```python
builders = {
    "ollama": ...,
    "anthropic": ...,
    "openai": ...,
    "groq": ...,
    "mistral": _build_mistral_llm,   # ← add here
}
```

4. Add settings to `config/settings.py`:
```python
mistral_api_key: str = ""
mistral_model: str = "mistral-large-latest"
```

5. Add to `.env.example`:
```env
MISTRAL_API_KEY=...
MISTRAL_MODEL=mistral-large-latest
```

6. Update `LLM_PROVIDER` type hint in `settings.py`:
```python
llm_provider: Literal["ollama", "anthropic", "openai", "groq", "mistral"] = "ollama"
```

---

## State Management

### Backend — AgentState (LangGraph)

```python
class AgentState(TypedDict):
    messages: list[BaseMessage]    # Full conversation, managed by LangGraph
    session_id: str                # For DB/cache lookups in tools
    product_image_b64: str | None  # Passed to vision model
    product_context: str | None    # Cached product analysis JSON
    brand_guidelines: str | None   # Injected as system message
    current_phase: str | None      # For activity panel
    observations: list[str]        # Tool outputs this turn
    iteration: int                 # Safety counter
    max_iterations: int            # Default: 10
```

State is NOT persisted by LangGraph (no checkpointer). Instead:
- `messages` history → MongoDB `messages` collection
- `product_context` → Redis cache + MongoDB `products` collection
- `brand_guidelines` → MongoDB `sessions` collection

### Frontend — Zustand Store

```typescript
interface ChatStore {
    sessions: Session[]              // Sidebar list
    activeSessionId: string | null   // Currently open session
    messages: Record<string, Message[]>  // Keyed by sessionId
    isStreaming: boolean
    agentActivity: AgentActivity | null  // Live phase + tool calls
    pendingImageId: string | null    // Uploaded but not yet sent
    pendingImagePreview: string | null   // Object URL for preview
}
```

All state lives in memory (Zustand). MongoDB is the source of truth for history.

---

## SSE Streaming Architecture

FastAPI uses `sse-starlette` to serve Server-Sent Events:

```python
# Backend emits:
{"type": "phase",       "phase": "thinking",    "description": "Analysing your request..."}
{"type": "token",       "content": "Here"}
{"type": "token",       "content": " is"}
{"type": "tool_call",   "tool": "generate_html_poster", "args": {...}}
{"type": "tool_result", "tool": "generate_html_poster", "preview": "<!DOCTYPE...", "success": true}
{"type": "content",     "content_type": "poster", "content": "<!DOCTYPE html>..."}
{"type": "token",       "content": " Your poster is ready!"}
{"type": "done"}
```

Frontend receives via `EventSource` API (browser-native, no library needed):

```typescript
const es = new EventSource(`/api/chat/stream/${sessionId}?message=...`);
es.onmessage = (e) => {
    const event = JSON.parse(e.data);
    // Route by event.type
};
```

**Why SSE instead of WebSockets?**
- Unidirectional (server → client) which matches our use case perfectly
- Built into browsers with no polyfill
- Works through proxies and load balancers reliably
- Auto-reconnect behaviour built in

---

## MongoDB Schema

```
marketing_agent DB
├── sessions
│   ├── session_id: string (unique index)
│   ├── name: string
│   ├── product_name: string | null
│   ├── product_image_url: string | null
│   ├── product_analysis: string | null
│   ├── brand_guidelines: string | null
│   ├── created_at: Date
│   ├── updated_at: Date
│   └── message_count: int
│
├── messages
│   ├── _id: ObjectId
│   ├── session_id: string (index)
│   ├── role: "user" | "assistant" | "system"
│   ├── content: string
│   ├── image_id: string | null
│   ├── metadata: object
│   └── created_at: Date (index)
│
├── generated_content
│   ├── _id: ObjectId
│   ├── session_id: string (index)
│   ├── content_type: "poster"|"social_post"|"description"|"hashtags"|"campaign"|"ad_copy"
│   ├── content: string (the actual content)
│   ├── platform: string | null
│   ├── metadata: object
│   └── created_at: Date
│
└── products
    ├── _id: ObjectId
    ├── session_id: string (index)
    ├── name: string | null
    ├── analysis: string (JSON from vision LLM)
    ├── image_id: string
    ├── image_url: string
    └── created_at: Date
```

---

## Redis Caching Strategy

| Key pattern                    | Value              | TTL    | Purpose                         |
|--------------------------------|--------------------|--------|---------------------------------|
| `product:{session_id}:analysis`| JSON string        | 24h    | Avoids re-analysing same image  |
| `session:{session_id}:context` | JSON object        | 24h    | Quick session metadata access   |

Redis is cache-only — MongoDB is the source of truth. If Redis is down, the app
falls back to MongoDB for all reads. Cache misses are handled gracefully.

---

## Common Issues

### "LLM not responding" / Ollama timeout

```bash
# Check Ollama is running
ollama list              # See pulled models
ollama ps               # See running models

# If no models are loaded:
ollama pull gemma3:12b
ollama pull llava:13b

# Then update backend/.env:
OLLAMA_MODEL=gemma3:12b
OLLAMA_VISION_MODEL=llava:13b
```

### Redis connection refused

```bash
# Start Redis
redis-server --daemonize yes

# Verify
redis-cli ping   # Should return PONG
```

### MongoDB connection refused

```bash
# Start MongoDB
mongod --dbpath /usr/local/var/mongodb --logpath /usr/local/var/log/mongodb/mongo.log --fork

# Or with brew services (macOS):
brew services start mongodb-community
```

### SSE stream closes immediately

Check CORS settings in `backend/.env`:
```env
CORS_ORIGINS=http://localhost:3000
```

### Poster not rendering

The poster is returned as raw HTML. If the iframe shows a blank page:
1. Open browser DevTools → Network tab
2. Check the `content` event payload contains `<!DOCTYPE html>`
3. If content is truncated, increase `max_tokens` in the LLM config

### "Tool not found" error

If you add a new tool, restart the backend — tools are registered at startup when
`get_agent_graph()` is called. Hot-reload won't re-compile the graph.

### Images not working with Ollama

Not all Ollama models support vision. Make sure `OLLAMA_VISION_MODEL` points to a
multimodal model:
```bash
ollama pull llava:13b      # LLaVA - vision capable
ollama pull llava:34b      # Higher quality vision
ollama pull gemma3:27b     # Gemma 3 also supports vision
```

Test vision is working:
```bash
curl http://localhost:11434/api/generate -d '{
  "model": "llava:13b",
  "prompt": "What is in this image?",
  "images": ["<base64_image_data>"]
}'
```
