"""
server.py - GitHub Agent root entry-point.
"""

import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - local fallback
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if load_dotenv:
    load_dotenv(BASE_DIR / ".env")

from api.server import app
from ec2_shared.oauth_router import OAuthAgentRegistration, register_oauth_routes

register_oauth_routes(
    app,
    OAuthAgentRegistration(
        provider="github",
        agent_slug="github",
        display_name="GitHub",
        default_scopes=["repo", "read:user", "user:email"],
    ),
)

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8006"))
    print(f"Starting GitHub Agent on port {port}")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
