"""
server.py — Day Planner Agent root entry-point.
Mirrors the teams-agent/server.py pattern.

Run with:
    python server.py
Or directly:
    uvicorn api.server:app --host 0.0.0.0 --port 8002
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from api.server import app  # noqa: F401 — re-exports the FastAPI app

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8002"))
    print(f"Starting Day Planner Agent on port {port}")
    uvicorn.run("api.server:app", host="0.0.0.0", port=port, reload=False)
