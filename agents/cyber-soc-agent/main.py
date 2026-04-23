"""
Launcher for deployment environments expecting main.py at agent root.
"""

import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8043"))
    print(f"Starting Cyber SOC Agent on port {port}")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)

