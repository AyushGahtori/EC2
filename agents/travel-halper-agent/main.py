import os


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8040"))
    print(f"Starting Travel Halper Agent on port {port}")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
