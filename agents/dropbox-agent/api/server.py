"""
api/server.py — Dropbox Agent
Converted from: marketplace copy/backend/tools/dropbox.js

Auth: OAuth2 (DROPBOX_CLIENT_ID / DROPBOX_CLIENT_SECRET)
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

app = FastAPI(title="Dropbox Agent API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DROPBOX_API = "https://api.dropboxapi.com/2"


class AgentTaskRequest(BaseModel):
    taskId: str
    userId: str
    agentId: str
    action: str
    access_token: str | None = None
    # search_files
    query: str | None = None
    limit: int | None = None
    # create_folder
    path: str | None = None
    # move_file
    from_path: str | None = None
    to_path: str | None = None
    model_config = ConfigDict(extra="allow")


class AgentTaskResponse(BaseModel):
    status: str
    type: str | None = None
    error: str | None = None
    message: str | None = None
    data: dict | None = None
    displayName: str | None = None


@app.post("/dropbox/action", response_model=AgentTaskResponse)
def execute_dropbox_action(req: AgentTaskRequest) -> AgentTaskResponse:
    """
    Mirrors the execute() function in dropbox.js.
    Calls Dropbox API v2 endpoints.
    """
    action = req.action
    token = req.access_token

    if not token:
        return AgentTaskResponse(
            status="failed",
            error="Dropbox access token is missing.",
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        # ── search_files ──────────────────────────────────────────────────────
        if action == "search_files":
            if not req.query:
                return AgentTaskResponse(status="failed", error="query is required.")

            # POST /files/search_v2
            payload = {
                "query": req.query,
                "options": {"max_results": req.limit or 10},
            }
            res = requests.post(
                f"{DROPBOX_API}/files/search_v2",
                headers=headers,
                json=payload,
                timeout=15,
            )
            res.raise_for_status()
            data = res.json()
            matches = data.get("matches", [])
            return AgentTaskResponse(
                status="success",
                type="dropbox_files",
                message=f"Found {len(matches)} result(s) for '{req.query}'.",
                data=data,
                displayName="Search Results",
            )

        # ── create_folder ─────────────────────────────────────────────────────
        elif action == "create_folder":
            if not req.path:
                return AgentTaskResponse(status="failed", error="path is required.")

            # POST /files/create_folder_v2
            payload = {"path": req.path, "autorename": True}
            res = requests.post(
                f"{DROPBOX_API}/files/create_folder_v2",
                headers=headers,
                json=payload,
                timeout=15,
            )
            res.raise_for_status()
            data = res.json()
            return AgentTaskResponse(
                status="success",
                type="dropbox_action",
                message=f"Folder '{req.path}' created successfully.",
                data=data,
                displayName=req.path,
            )

        # ── move_file ─────────────────────────────────────────────────────────
        elif action == "move_file":
            if not req.from_path or not req.to_path:
                return AgentTaskResponse(
                    status="failed", error="from_path and to_path are both required."
                )

            # POST /files/move_v2
            payload = {
                "from_path": req.from_path,
                "to_path": req.to_path,
                "autorename": True,
            }
            res = requests.post(
                f"{DROPBOX_API}/files/move_v2",
                headers=headers,
                json=payload,
                timeout=15,
            )
            res.raise_for_status()
            data = res.json()
            return AgentTaskResponse(
                status="success",
                type="dropbox_action",
                message=f"Moved file to '{req.to_path}'.",
                data=data,
                displayName=req.to_path,
            )

        else:
            return AgentTaskResponse(status="failed", error=f"Unknown action: {action}")

    except requests.exceptions.HTTPError as e:
        logger.exception("Dropbox API HTTP error")
        return AgentTaskResponse(status="failed", error=f"Dropbox API error: {e.response.text}")
    except Exception as e:
        logger.exception("Dropbox agent error")
        return AgentTaskResponse(status="failed", error=str(e))


@app.get("/health")
def health():
    return {"status": "healthy", "agent": "dropbox-agent"}
