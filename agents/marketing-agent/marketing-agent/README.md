# 🤖 Autonomous Marketing AI Agent

A fully autonomous, LLM-powered marketing agent that analyzes product images and generates professional marketing content (posters, descriptions, social media posts) for Instagram, LinkedIn, Twitter, and more. Built with a ChatGPT-like UI and true agentic ReAct behavior.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────┐
│                 Next.js 14 Frontend                  │
│         ChatGPT-like UI with SSE Streaming           │
└─────────────────────┬───────────────────────────────┘
                      │ HTTP / SSE
┌─────────────────────▼───────────────────────────────┐
│                  FastAPI Backend                      │
│              REST + SSE Endpoints                    │
└─────────────────────┬───────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────┐
│            LangGraph ReAct Agent                     │
│  THINK → PLAN → ACT → OBSERVE → REFLECT → PERFORM   │
│           (LLM drives ALL decisions)                 │
└──────┬──────────────┬──────────────┬────────────────┘
       │              │              │
┌──────▼──────┐ ┌─────▼──────┐ ┌───▼────────────────┐
│ LLM Service │ │   Tools     │ │  State Management  │
│  (Ollama /  │ │ - Analyze   │ │  Redis + MongoDB   │
│  Anthropic  │ │ - Describe  │ │                    │
│  / OpenAI)  │ │ - Poster    │ │                    │
│             │ │ - Social    │ │                    │
│             │ │ - Hashtags  │ │                    │
└─────────────┘ └────────────┘ └────────────────────┘
```

### ReAct Cycle (No Hardcoded Logic)
```
User Input + Product Image
         │
         ▼
    🧠 THINK   → LLM analyzes intent, context, available tools
         │
         ▼
    📋 PLAN    → LLM creates step-by-step action plan
         │
         ▼
    ⚡ ACT     → LLM selects & calls appropriate tool
         │
         ▼
    👁️ OBSERVE → LLM processes tool result, updates understanding
         │
         ▼
    🔄 REFLECT → Is goal accomplished? If no → loop back to THINK
         │
         ▼
    ✅ PERFORM → Generate final polished response
```

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 14, TailwindCSS, TypeScript |
| Backend | Python 3.11+, FastAPI, Uvicorn |
| Agent | LangGraph, LangChain |
| LLM (default) | Ollama (`gemma4:27b` + `llava` for vision) |
| LLM (alt) | Anthropic Claude, OpenAI GPT-4o, Groq |
| Streaming | Server-Sent Events (SSE) |
| Cache | Redis |
| Database | MongoDB |
| Images | Pillow, base64 encoding |

---

## 📋 Prerequisites

- Python 3.11+
- Node.js 18+
- Redis (running on localhost:6379)
- MongoDB (running on localhost:27017)
- [Ollama](https://ollama.ai) installed and running

### Pull Required Ollama Models
```bash
# Main language model (text generation)
ollama pull gemma3:27b        # or gemma4:27b when available

# Vision model (image analysis)
ollama pull llava:34b         # or llava:13b for lighter version

# Alternative smaller models
ollama pull gemma3:12b
ollama pull llava:13b
```

---

## 🚀 Quick Setup

```bash
# Clone and setup
git clone <repo>
cd marketing-agent
chmod +x setup.sh
./setup.sh
```

Or manually:

### Backend
```bash
cd backend
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your settings
uvicorn main:app --reload --port 8010
```

### Frontend
```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

Open [http://localhost:3000](http://localhost:3000)

---

## 🔧 Configuration (`.env`)

```env
# Default provider (ollama | anthropic | openai | groq)
LLM_PROVIDER=ollama

# To switch providers, change LLM_PROVIDER and add the API key
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GROQ_API_KEY=gsk_...
```

---

## 🎯 Features

- **📸 Product Image Analysis** — Describe any product from a photo
- **📝 Marketing Copy** — Tone-aware, audience-targeted descriptions
- **🎨 HTML/CSS Posters** — Downloadable promotional posters
- **📱 Social Media Posts** — Optimized for Instagram, LinkedIn, Twitter, Pinterest
- **#️⃣ Hashtag Generator** — Platform-specific trending hashtags
- **🔄 Autonomous Reasoning** — Agent plans multi-step tasks independently
- **💬 Streaming Chat** — Real-time token streaming like ChatGPT
- **📂 Session History** — Persistent chat history with MongoDB
- **🖼️ Context Memory** — Agent remembers product across conversation

---

## 📁 Project Structure

```
marketing-agent/
├── backend/
│   ├── main.py                    # FastAPI app entry point
│   ├── requirements.txt
│   ├── .env.example
│   ├── config/settings.py         # Pydantic settings
│   ├── models/schemas.py          # Data models
│   ├── services/
│   │   ├── llm_service.py         # Multi-provider LLM abstraction
│   │   ├── redis_service.py       # Redis caching
│   │   └── mongodb_service.py     # MongoDB operations
│   ├── agents/
│   │   ├── state.py               # LangGraph state definition
│   │   ├── tools.py               # Agent tools (LLM-callable)
│   │   └── marketing_agent.py     # LangGraph ReAct agent
│   └── api/routes/
│       ├── chat.py                # Chat + SSE streaming
│       ├── upload.py              # Image upload
│       └── sessions.py            # Session management
└── frontend/
    └── src/
        ├── app/                   # Next.js App Router
        ├── components/            # React components
        │   ├── ChatInterface.tsx
        │   ├── MessageBubble.tsx
        │   ├── Sidebar.tsx
        │   ├── ImageUpload.tsx
        │   └── ContentPreview.tsx
        ├── hooks/useChat.ts       # Chat state hook
        └── lib/api.ts             # API client
```
