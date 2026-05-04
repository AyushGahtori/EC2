# 🎙️ ARIA — AI Podcast Agent

> A production-ready AI agent that acts as a **real-time podcast host** (voice conversation) and **podcast creator** (text-based assistant). Built with LangGraph ReAct, FastAPI, Next.js, Redis, and MongoDB.

---

## ✨ Features

| Feature | Description |
|---|---|
| 🎙️ **Host Mode** | Real-time voice podcast host — conversational, emotional, engaging |
| ✍️ **Creator Mode** | ChatGPT-like interface for generating scripts, outlines, research |
| 🧠 **ReAct Agent** | LangGraph-based Think → Act → Observe loop with 5 tools |
| 🔊 **Voice Pipeline** | Mic → Whisper STT → Agent → TTS → Playback |
| 🗃️ **Memory** | Redis (session) + MongoDB (persistent) |
| 🔄 **Multi-LLM** | Ollama / OpenAI / Anthropic / Gemini — switch via ENV |
| 📡 **Streaming** | Server-sent events for real-time token streaming |

---

## 🏗️ Project Structure

```
aria-podcast-agent/
├── backend/
│   ├── app/
│   │   ├── main.py                  ← FastAPI app entry
│   │   ├── routes/
│   │   │   ├── chat.py              ← Chat endpoints
│   │   │   ├── voice.py             ← Voice input endpoint
│   │   │   └── tts.py               ← Text-to-speech endpoint
│   │   ├── agents/
│   │   │   ├── react_agent.py       ← LangGraph ReAct agent
│   │   │   └── tools.py             ← Agent tools (search, wiki, scripts…)
│   │   ├── services/
│   │   │   ├── llm_service.py       ← Multi-provider LLM factory
│   │   │   ├── memory_service.py    ← Redis + MongoDB
│   │   │   ├── tts_service.py       ← RapidAPI + gTTS fallback
│   │   │   └── stt_service.py       ← faster-whisper STT
│   │   └── utils/
│   │       └── host_controller.py   ← Mode detection + prompts
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── pages/
│   │   ├── _app.js
│   │   └── index.js                 ← Main UI
│   ├── components/
│   │   ├── ChatWindow.js
│   │   ├── MessageBubble.js
│   │   ├── ModeToggle.js
│   │   ├── MicButton.js
│   │   └── AudioPlayer.js
│   ├── hooks/
│   │   ├── useChat.js
│   │   └── useVoice.js
│   ├── services/
│   │   └── api.js
│   ├── styles/
│   │   └── globals.css
│   ├── package.json
│   └── .env.local.example
└── README.md
```

---

## ⚡ Quick Start

### Prerequisites

- **Python 3.10+**
- **Node.js 18+**
- **Redis** running locally
- **MongoDB** running locally
- One of: **Ollama** (local), or API key for OpenAI / Anthropic / Gemini

---

### 1. Install System Dependencies

**Redis:**
```bash
# macOS
brew install redis && brew services start redis

# Ubuntu/Debian
sudo apt install redis-server && sudo systemctl start redis
```

**MongoDB:**
```bash
# macOS
brew install mongodb-community && brew services start mongodb-community

# Ubuntu/Debian
sudo apt install mongodb && sudo systemctl start mongodb
```

**Ollama (if using local LLM):**
```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull a model (choose one)
ollama pull gemma3:4b        # Recommended (fast, good quality)
ollama pull llama3.2:3b      # Lighter alternative
ollama pull mistral:7b       # Higher quality
```

**FFmpeg (required for audio processing):**
```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg
```

---

### 2. Backend Setup

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate          # Linux/macOS
# venv\Scripts\activate           # Windows

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your settings (see Configuration below)
```

---

### 3. Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Configure environment
cp .env.local.example .env.local
# Edit if your backend runs on a different port
```

---

### 4. Configure Environment

Edit `backend/.env`:

```env
# Choose your LLM provider
LLM_PROVIDER=ollama          # or: openai | anthropic | gemini
LLM_MODEL=gemma3:4b          # model name for your provider

# Fill the key for your provider
OPENAI_API_KEY=sk-...
# ANTHROPIC_API_KEY=sk-ant-...
# GOOGLE_API_KEY=AIza...

# Voice TTS (optional — falls back to gTTS if not set)
RAPIDAPI_KEY=your_key_here

# Databases (defaults work if running locally)
REDIS_URL=redis://localhost:6379
MONGODB_URL=mongodb://localhost:27017
```

---

### 5. Run the Application

**Terminal 1 — Backend:**
```bash
cd backend
source venv/bin/activate
python -m app.main
# Server starts at http://localhost:8000
# Swagger docs at http://localhost:8000/docs
```

**Terminal 2 — Frontend:**
```bash
cd frontend
npm run dev
# UI starts at http://localhost:3000
```

Open **http://localhost:3000** in your browser.

---

## 🎭 Using ARIA

### Host Mode 🎙️
1. Click **"🎙️ Host"** in the sidebar toggle
2. ARIA greets you as a podcast host
3. **Text:** Type your message and press Enter
4. **Voice:** Click the 🎤 mic button, speak, click again to stop
5. ARIA responds in character — use the **▶ Listen** button to hear the response

### Creator Mode ✍️
1. Click **"✍️ Creator"** in the sidebar toggle
2. Ask ARIA to:
   - `"Write a 20-minute podcast script on quantum computing"`
   - `"Give me 10 trending podcast topic ideas for 2025"`
   - `"Create an episode structure about mental health for founders"`
   - `"Research the history of podcasting and write show notes"`

---

## 🔧 Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama` \| `openai` \| `anthropic` \| `gemini` |
| `LLM_MODEL` | `gemma3:4b` | Model name for the chosen provider |
| `RAPIDAPI_KEY` | — | RapidAPI key for Voixor TTS (optional) |
| `WHISPER_MODEL` | `base` | `tiny` / `base` / `small` / `medium` |
| `WHISPER_DEVICE` | `cpu` | `cpu` or `cuda` |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection URL |
| `MONGODB_URL` | `mongodb://localhost:27017` | MongoDB connection URL |
| `AGENT_MAX_STEPS` | `5` | Max ReAct loop iterations |
| `SESSION_TTL_SECONDS` | `3600` | Session memory TTL |

---

## 🧠 Agent Tools

| Tool | Description |
|---|---|
| `web_search` | DuckDuckGo search — no API key needed |
| `wikipedia_search` | Wikipedia summaries for facts/context |
| `generate_podcast_script` | Full podcast script generation |
| `summarize_content` | Distil long text into key points |
| `format_podcast_structure` | Structure raw notes into episode format |
| `memory_read` | Read session history |
| `memory_write` | Write notes to memory |

---

## 🔊 Voice Pipeline

```
Microphone
  → MediaRecorder (browser, WebM/Opus)
  → POST /api/voice/input
  → faster-whisper (local STT)
  → LangGraph ReAct Agent
  → Text response
  → POST /api/tts/
  → RapidAPI Voixor (primary) or gTTS (fallback)
  → MP3 audio stream
  → Browser Audio API playback
```

---

## 🌐 API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/chat/` | Send chat message (supports streaming) |
| `POST` | `/api/chat/mode` | Switch mode (host/creator) |
| `POST` | `/api/chat/welcome` | Get mode welcome message |
| `GET` | `/api/chat/history/{id}` | Get session history |
| `DELETE` | `/api/chat/history/{id}` | Clear session |
| `POST` | `/api/voice/input` | Upload audio → transcribe → respond |
| `POST` | `/api/tts/` | Text → MP3 audio |
| `GET` | `/health` | System health check |
| `GET` | `/docs` | Swagger UI |

---

## 🛠️ Troubleshooting

**Ollama model not found:**
```bash
ollama list              # see installed models
ollama pull gemma3:4b    # install
```

**Redis connection refused:**
```bash
redis-cli ping           # should return PONG
brew services restart redis   # macOS
```

**MongoDB connection refused:**
```bash
mongosh                  # test connection
brew services restart mongodb-community  # macOS
```

**Whisper model download on first run:**
- First STT request downloads the model (~150MB for `base`) — this is normal

**Mic not working:**
- Browser needs HTTPS or localhost for microphone access
- Check browser permissions (click the lock icon in address bar)

**gTTS requires internet:**
- gTTS fallback uses Google's TTS API — needs an internet connection

---

## 📦 Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+, FastAPI, Uvicorn |
| AI Agent | LangGraph (ReAct), LangChain |
| LLMs | Ollama / OpenAI / Anthropic / Gemini |
| STT | faster-whisper (local) |
| TTS | RapidAPI Voixor + gTTS fallback |
| Memory | Redis (session) + MongoDB (persistent) |
| Frontend | Next.js 14, React 18, Tailwind CSS |
| Animations | Framer Motion |

---

## 📄 License

MIT — build whatever you want with it.

---

*Built with ❤️ — ARIA Podcast Agent*
