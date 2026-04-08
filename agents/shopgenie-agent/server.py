from __future__ import annotations

import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from api.server import app  # noqa: F401


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8021"))
    print(f"Starting ShopGenie Agent on port {port}")
    uvicorn.run("api.server:app", host="0.0.0.0", port=port, reload=False)
