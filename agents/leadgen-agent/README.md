# 🤖 LeadGen AI Agent

An **autonomous** AI agent for lead generation built with **LangGraph + ReAct reasoning**.
No hardcoded workflows — the LLM decides every step autonomously.

---

## 🧠 How It Works

```
User Query → Planner Node → Tool Node → Reflection Node → Decision → Loop or End
```

The agent uses **ReAct** (Reason + Act) pattern:
1. **PLAN** — LLM analyzes the query and decides what to do
2. **ACT** — Executes the chosen tool (search, enrich, score, store)
3. **OBSERVE** — Reads tool output
4. **REFLECT** — Evaluates progress, decides next action
5. **REPEAT** — Loops until task complete or max iterations reached

Everything is LLM-driven. The graph structure only provides rails.

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11+ · FastAPI · LangGraph |
| Agent | LangGraph StateGraph · ReAct + Self-Reflection |
| LLM | Ollama (default) · OpenAI · Anthropic |
| Database | MongoDB (leads) · Redis (memory + cache) |
| Frontend | Next.js 14 · Tailwind CSS · SSE streaming |
| Search | Serper API (Google + LinkedIn + Maps) |
| Enrichment | Tavily API |

---

## 📁 Project Structure

```
leadgen-agent/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI app entry
│   │   ├── agent/
│   │   │   ├── graph.py            # LangGraph definition
│   │   │   ├── nodes.py            # planner/tool/reflection/decision nodes
│   │   │   ├── state.py            # AgentState TypedDict
│   │   │   └── prompts.py          # All LLM prompts
│   │   ├── tools/
│   │   │   ├── google_search.py    # Serper web search
│   │   │   ├── google_maps.py      # Serper Maps API
│   │   │   ├── linkedin_search.py  # Google → site:linkedin.com/in
│   │   │   ├── company_enrichment.py # Tavily deep enrichment
│   │   │   ├── email_finder.py     # Website scraping + patterns
│   │   │   ├── lead_scoring.py     # LLM-based ICP scoring
│   │   │   └── storage.py          # MongoDB persistence
│   │   ├── services/
│   │   │   ├── llm_provider.py     # Dynamic LLM (Ollama/OpenAI/Anthropic)
│   │   │   ├── redis_client.py     # Memory + caching
│   │   │   └── mongodb_client.py   # Async MongoDB operations
│   │   ├── config/
│   │   │   └── settings.py         # Pydantic settings
│   │   └── api/
│   │       └── routes.py           # FastAPI routes
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx                # Split-panel layout
│   │   ├── globals.css
│   │   └── components/
│   │       ├── Chat.tsx            # Chat interface + SSE
│   │       ├── Message.tsx         # Message bubble with Markdown
│   │       ├── InputBox.tsx        # Auto-resize textarea
│   │       └── LeadTable.tsx       # Sortable leads table + CSV export
│   ├── package.json
│   └── next.config.js
└── README.md
```

---

## ⚙️ Prerequisites

Install these before setup:

| Tool | Download |
|------|----------|
| Python 3.11+ | https://python.org |
| Node.js 18+ | https://nodejs.org |
| MongoDB | https://www.mongodb.com/try/download/community |
| Redis (Windows) | https://github.com/microsoftarchive/redis/releases OR use Memurai |
| Ollama (optional) | https://ollama.ai |

---

## 🚀 Setup & Running

### Step 1 — Clone / Extract

```cmd
cd C:\
unzip leadgen-agent.zip
cd leadgen-agent
```

### Step 2 — Backend Setup

```cmd
cd backend

:: Create virtual environment
python -m venv venv

:: Activate it
venv\Scripts\activate

:: Install dependencies
pip install -r requirements.txt

:: Copy and configure environment
copy .env.example .env
notepad .env
```

**Edit `.env`** with your API keys (see API Keys section below).

### Step 3 — Start Backend

```cmd
:: Make sure venv is active
cd backend
venv\Scripts\activate

python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

✅ Backend available at: http://localhost:8000
📖 API docs at: http://localhost:8000/docs

### Step 4 — Frontend Setup

Open a **new terminal**:

```cmd
cd frontend

:: Install dependencies
npm install

:: Copy env
copy .env.local.example .env.local
```

### Step 5 — Start Frontend

```cmd
cd frontend
npm run dev
```

✅ Frontend available at: http://localhost:3000

---

## 🔑 API Keys

### Required for search tools:

| Key | Where to Get | Used For |
|-----|-------------|----------|
| `SERPER_API_KEY` | https://serper.dev | Google Search, LinkedIn, Maps |
| `TAVILY_API_KEY` | https://app.tavily.com | Company enrichment |

Serper offers 2,500 free queries/month.
Tavily offers 1,000 free searches/month.

### LLM Providers (pick one):

**Option A — Ollama (Free, Local)**
```env
MODEL_PROVIDER=ollama
MODEL_NAME=qwen3.5:397b-cloud
OLLAMA_BASE_URL=http://localhost:11434
```
Install model: `ollama pull qwen3.5:397b-cloud`

**Option B — OpenAI**
```env
MODEL_PROVIDER=openai
MODEL_NAME=gpt-4o-mini
OPENAI_API_KEY=sk-...
```

**Option C — Anthropic**
```env
MODEL_PROVIDER=anthropic
MODEL_NAME=claude-3-5-haiku-20241022
ANTHROPIC_API_KEY=sk-ant-...
```

---

## 💬 Example Queries

```
Find 50 SaaS founders in India with emails
Find B2B software CTOs in Singapore who raised Series A
Find 20 fintech startup CEOs in London with LinkedIn profiles
Find e-commerce founders in the US with company websites
Find 30 HR tech founders in Europe
```

---

## 🗄️ Database

**MongoDB** — Collection: `leadgen.leads`

| Field | Type | Description |
|-------|------|-------------|
| name | string | Full name |
| title | string | Job title |
| company | string | Company name |
| email | string | Email address |
| phone | string | Phone number |
| linkedin_url | string | LinkedIn profile URL |
| website | string | Company website |
| industry | string | Industry vertical |
| company_size | string | Employee count |
| score | integer | ICP fit score (0–100) |
| session_id | string | Session identifier |
| source | string | How it was found |
| created_at | string | ISO timestamp |

---

## 🌐 API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/chat/stream` | Stream agent response (SSE) |
| POST | `/api/chat` | Non-streaming chat |
| GET | `/api/leads` | List stored leads |
| GET | `/api/leads?session_id=X` | Filter by session |
| GET | `/api/session/new` | Generate new session |
| GET | `/api/health` | Health check |
| GET | `/docs` | Swagger API docs |

---

## 🔧 Configuration

Key `.env` settings:

```env
MAX_ITERATIONS=20        # Max agent loop iterations (increase for bigger tasks)
MONGODB_DB=leadgen       # Database name
REDIS_URL=redis://localhost:6379
```

---

## 🐛 Troubleshooting

**Backend won't start**
- Ensure Python 3.11+ is installed: `python --version`
- Ensure venv is activated: look for `(venv)` in prompt
- Check MongoDB is running: `mongod --version`
- Check Redis is running

**Agent finds no leads**
- Ensure `SERPER_API_KEY` is set in `.env`
- Test Serper API at https://serper.dev

**LLM errors with Ollama**
- Ensure Ollama is running: `ollama serve`
- Pull the model: `ollama pull qwen3.5:397b-cloud`
- Check: http://localhost:11434

**Frontend can't connect**
- Ensure backend is running on port 8000
- Check CORS: `CORS_ORIGINS=http://localhost:3000` in backend `.env`

---

## 📤 Exporting Leads

1. Click **"Export CSV"** button in the Leads table
2. Or call the API: `GET /api/leads?limit=500`
3. Direct MongoDB query: `db.leads.find({}).sort({score:-1})`

---

## 🤖 Agent Behavior (No Hardcoded Logic)

The agent uses **zero** hardcoded if/else workflows. Instead:

- The **LLM reads the query** and decides what tools to call
- **Reflection node** evaluates progress after each tool call
- **Decision node** checks: reached target? Reflection says done? Max iterations?
- The agent **self-corrects** if a search strategy isn't working

This means the agent handles ANY lead generation query dynamically.
