class Config:
    MODEL_NAME = (
        __import__("os").getenv("RESTAURANT_CONCIERGE_MODEL")
        or __import__("os").getenv("GEMINI_MODEL_FLASH")
        or __import__("os").getenv("GEMINI_MODEL")
        or __import__("os").getenv("GEMINI_MODEL_PRO")
        or "gemini-2.5-flash"
    )
    MODEL_TEMPERATURE = float(
        (__import__("os").getenv("RESTAURANT_CONCIERGE_TEMPERATURE") or "0.4").strip() or "0.4"
    )
    GOOGLE_API_KEY = (
        __import__("os").getenv("GEMINI_API_KEY")
        or __import__("os").getenv("GOOGLE_API_KEY")
        or ""
    ).strip()
    MENU_FILE_PATH = "src/data/menu.json"
    SESSION_TTL_SECONDS = int(
        (__import__("os").getenv("RESTAURANT_CONCIERGE_SESSION_TTL_SECONDS") or "43200").strip()
        or "43200"
    )
    MAX_HISTORY_ITEMS = int(
        (__import__("os").getenv("RESTAURANT_CONCIERGE_MAX_HISTORY") or "80").strip() or "80"
    )
    DISPLAY_NAME = (
        __import__("os").getenv("RESTAURANT_CONCIERGE_DISPLAY_NAME") or "Restaurant Concierge"
    ).strip()
