"""
main.py - Google Agent launcher for deployments that expect a root main module.
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8300"))
    print(f"Starting Google Agent on port {port}")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
