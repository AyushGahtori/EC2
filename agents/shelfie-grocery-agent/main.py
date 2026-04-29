import os

from dotenv import load_dotenv

load_dotenv()

from server import app  # noqa: E402,F401


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8045"))
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False)
