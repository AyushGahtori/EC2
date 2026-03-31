"""
api/server.py — Discord Agent
Converted from: marketplace copy/backend/tools/discord.js

Auth: OAuth2 (DISCORD_CLIENT_ID / DISCORD_CLIENT_SECRET)
Scopes: identify, guilds
"""
from __future__ import annotations

import logging

import requests
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

load_dotenv()
logger = logging.getLogger(__name__)

app = FastAPI(title="Discord Agent API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DISCORD_API = "https://discord.com/api/v10"


class AgentTaskRequest(BaseModel):
    taskId: str
    userId: str
    agentId: str
    action: str
    access_token: str | None = None
    model_config = ConfigDict(extra="allow")


class AgentTaskResponse(BaseModel):
    status: str
    type: str | None = None
    error: str | None = None
    message: str | None = None
    data: dict | None = None
    displayName: str | None = None


@app.post("/discord/action", response_model=AgentTaskResponse)
def execute_discord_action(req: AgentTaskRequest) -> AgentTaskResponse:
    """
    Mirrors the execute() function in discord.js.
    Calls Discord REST API v10 with the user's OAuth2 Bearer token.
    """
    action = req.action
    token = req.access_token

    if not token:
        return AgentTaskResponse(
            status="failed",
            error="Discord access token is missing. Please connect your Discord account.",
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        # ── get_user_info ─────────────────────────────────────────────────────
        if action == "get_user_info":
            # GET /users/@me
            res = requests.get(f"{DISCORD_API}/users/@me", headers=headers, timeout=10)
            res.raise_for_status()
            d = res.json()
            return AgentTaskResponse(
                status="success",
                type="discord_user",
                message=f"Fetched Discord profile for {d.get('username')}.",
                data={
                    "id": d.get("id"),
                    "username": d.get("username"),
                    "discriminator": d.get("discriminator"),
                    "global_name": d.get("global_name"),
                    "mfa_enabled": d.get("mfa_enabled"),
                    "locale": d.get("locale"),
                    "premium_type": d.get("premium_type"),
                },
                displayName=d.get("username"),
            )

        # ── list_guilds ───────────────────────────────────────────────────────
        elif action == "list_guilds":
            # GET /users/@me/guilds
            res = requests.get(f"{DISCORD_API}/users/@me/guilds", headers=headers, timeout=10)
            res.raise_for_status()
            guilds = res.json()
            guild_list = [
                {
                    "id": g.get("id"),
                    "name": g.get("name"),
                    "isOwner": g.get("owner"),
                    "permissions": g.get("permissions"),
                }
                for g in guilds
            ]
            return AgentTaskResponse(
                status="success",
                type="discord_guilds",
                message=f"You are a member of {len(guild_list)} Discord server(s).",
                data={"guilds": guild_list},
                displayName="Discord Servers",
            )

        else:
            return AgentTaskResponse(status="failed", error=f"Unknown action: {action}")

    except requests.exceptions.HTTPError as e:
        logger.exception("Discord API HTTP error")
        return AgentTaskResponse(status="failed", error=f"Discord API error: {e.response.text}")
    except Exception as e:
        logger.exception("Discord agent error")
        return AgentTaskResponse(status="failed", error=str(e))


@app.get("/health")
def health():
    return {"status": "healthy", "agent": "discord-agent"}
