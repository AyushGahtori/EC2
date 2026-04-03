"""Process entrypoint for strata-agent."""

import os

from dotenv import load_dotenv

load_dotenv()


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8012"))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
