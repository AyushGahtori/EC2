"""
api/server.py — Canva Agent
Converted from: marketplace copy/backend/tools/canva.js

Auth: OAuth2 (CANVA_CLIENT_ID / CANVA_CLIENT_SECRET)
Status: coming_soon — Canva API is not yet publicly available.
"""
from __future__ import annotations

import logging
import os

import requests
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

load_dotenv()
logger = logging.getLogger(__name__)

app = FastAPI(title="Canva Agent API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CANVA_API_BASE = "https://api.canva.com/rest/v1"


class AgentTaskRequest(BaseModel):
    taskId: str
    userId: str
    agentId: str
    action: str
    # OAuth token passed in by the web app after user grants access
    access_token: str | None = None
    # Function-specific fields
    limit: int | None = None
    title: str | None = None
    type: str | None = None
    model_config = ConfigDict(extra="allow")


class AgentTaskResponse(BaseModel):
    status: str
    type: str | None = None
    error: str | None = None
    message: str | None = None
    data: dict | None = None
    displayName: str | None = None


@app.post("/canva/action", response_model=AgentTaskResponse)
def execute_canva_action(req: AgentTaskRequest) -> AgentTaskResponse:
    """
    Routes incoming action to the correct Canva API call.
    Mirrors the execute() switch in canva.js.

    NOTE: Canva's API is currently in limited beta — the execute() in the
    original JS throws 'Canva integration is coming soon.' for all functions.
    We implement the real HTTP calls here so the integration is ready the
    moment Canva opens their API.
    """
    action = req.action

    # ── Auth guard ────────────────────────────────────────────────────────────
    token = req.access_token
    if not token:
        return AgentTaskResponse(
            status="failed",
            error="Canva access token is missing. Please connect your Canva account.",
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        # ── list_designs ──────────────────────────────────────────────────────
        if action == "list_designs":
            params = {"limit": req.limit or 10}
            res = requests.get(f"{CANVA_API_BASE}/designs", headers=headers, params=params, timeout=15)
            res.raise_for_status()
            designs = res.json().get("items", [])
            return AgentTaskResponse(
                status="success",
                type="canva_list",
                message=f"Found {len(designs)} design(s).",
                data={"designs": designs},
                displayName="Canva Designs",
            )

        # ── create_design ─────────────────────────────────────────────────────
        elif action == "create_design":
            if not req.title:
                return AgentTaskResponse(status="failed", error="title is required to create a design.")
            payload = {"title": req.title}
            if req.type:
                payload["design_type"] = {"type": req.type}
            res = requests.post(f"{CANVA_API_BASE}/designs", headers=headers, json=payload, timeout=15)
            res.raise_for_status()
            data = res.json()
            return AgentTaskResponse(
                status="success",
                type="canva_action",
                message=f"Design '{req.title}' created successfully.",
                data={"design": data},
                displayName=req.title,
            )

        else:
            return AgentTaskResponse(status="failed", error=f"Unknown action: {action}")

    except requests.exceptions.HTTPError as e:
        logger.exception("Canva API HTTP error")
        return AgentTaskResponse(status="failed", error=f"Canva API error: {e.response.text}")
    except Exception as e:
        logger.exception("Canva agent error")
        return AgentTaskResponse(status="failed", error=str(e))


@app.get("/health")
def health():
    return {"status": "healthy", "agent": "canva-agent"}
