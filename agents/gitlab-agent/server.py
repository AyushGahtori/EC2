"""
server.py - GitLab Agent root entry-point.
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
        provider="gitlab",
        agent_slug="gitlab",
        display_name="GitLab",
        default_scopes=["api", "read_user", "read_api"],
    ),
)

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8007"))
    print(f"Starting GitLab Agent on port {port}")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
