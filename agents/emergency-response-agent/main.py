"""
main.py - Emergency Response Agent launcher.
"""

import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8510"))
    print(f"Starting Emergency Response Agent on port {port}")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
