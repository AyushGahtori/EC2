"""
main.py — Canva Agent launcher (kept for backward-compat with deploy.sh).
Delegates to api/server.py FastAPI app.
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8001"))
    print(f"Starting Canva Agent on port {port}")
    uvicorn.run("api.server:app", host="0.0.0.0", port=port, reload=False)
