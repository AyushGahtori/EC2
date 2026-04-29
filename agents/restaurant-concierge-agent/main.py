from __future__ import annotations

import os

import uvicorn


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8044"))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
