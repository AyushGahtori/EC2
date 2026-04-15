import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8035"))
    print(f"Starting {os.getenv('DISPLAY_NAME', 'Fund Agent')} on port {port}")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)

