"""
api/server.py — Greenhouse Agent
Converted from: marketplace copy/backend/tools/greenhouse.js

Auth: API Key (Harvest API key passed as access_token)
      Greenhouse uses HTTP Basic Auth: base64(api_key + ':')
      The JS mirrors this with: Buffer.from(apiKey + ':').toString('base64')
"""
from __future__ import annotations

import base64
import logging

import requests
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

load_dotenv()
logger = logging.getLogger(__name__)

app = FastAPI(title="Greenhouse Agent API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GREENHOUSE_API = "https://harvest.greenhouse.io/v1"


class AgentTaskRequest(BaseModel):
    taskId: str
    userId: str
    agentId: str
    action: str
    access_token: str | None = None   # Greenhouse Harvest API key
    # list_candidates
    job_id: str | None = None
    candidate_status: str | None = None   # 'active', 'rejected', 'hired'
    # get_candidate_resume
    candidate_id: str | None = None
    # schedule_interview
    interviewer_email: str | None = None
    start_time: str | None = None    # ISO 8601
    end_time: str | None = None      # ISO 8601
    model_config = ConfigDict(extra="allow")


class AgentTaskResponse(BaseModel):
    status: str
    type: str | None = None
    error: str | None = None
    message: str | None = None
    data: dict | None = None
    displayName: str | None = None


def _greenhouse_session(api_key: str) -> requests.Session:
    """
    Creates a pre-authenticated session.
    Mirrors JS axiosInstance with Basic auth header + On-Behalf-Of header.
    """
    session = requests.Session()
    encoded = base64.b64encode(f"{api_key}:".encode()).decode()
    session.headers.update(
        {
            "Authorization": f"Basic {encoded}",
            "On-Behalf-Of": "system",  # Required for some Greenhouse POST actions
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
    )
    return session


@app.post("/greenhouse/action", response_model=AgentTaskResponse)
def execute_greenhouse_action(req: AgentTaskRequest) -> AgentTaskResponse:
    """
    Mirrors the execute() function in greenhouse.js.
    Calls the Greenhouse Harvest REST API v1.
    """
    action = req.action
    api_key = req.access_token

    if not api_key:
        return AgentTaskResponse(
            status="failed",
            error="Greenhouse requires a Harvest API key. Please connect your Greenhouse account.",
        )

    session = _greenhouse_session(api_key)

    try:
        # ── list_candidates ───────────────────────────────────────────────────
        if action == "list_candidates":
            # GET /candidates with optional job_id and status filters
            params: dict[str, str] = {}
            if req.job_id:
                params["job_id"] = req.job_id
            if req.candidate_status:
                params["status"] = req.candidate_status

            res = session.get(f"{GREENHOUSE_API}/candidates", params=params, timeout=15)
            res.raise_for_status()
            candidates = res.json()
            return AgentTaskResponse(
                status="success",
                type="greenhouse_list",
                message=f"Found {len(candidates)} candidate(s).",
                data={"candidates": candidates},
                displayName="Candidates",
            )

        # ── get_candidate_resume ──────────────────────────────────────────────
        elif action == "get_candidate_resume":
            if not req.candidate_id:
                return AgentTaskResponse(
                    status="failed", error="candidate_id is required."
                )
            # GET /candidates/{candidate_id}
            # Returns candidate data including attachment URLs (resume)
            res = session.get(f"{GREENHOUSE_API}/candidates/{req.candidate_id}", timeout=15)
            res.raise_for_status()
            candidate = res.json()
            # Extract attachments for resume
            attachments = candidate.get("attachments", [])
            resume_links = [a for a in attachments if a.get("type") == "resume"]
            return AgentTaskResponse(
                status="success",
                type="greenhouse_resume",
                message=f"Found candidate: {candidate.get('first_name')} {candidate.get('last_name')}.",
                data={
                    "id": candidate.get("id"),
                    "name": f"{candidate.get('first_name')} {candidate.get('last_name')}",
                    "emails": candidate.get("email_addresses", []),
                    "resume_links": resume_links,
                    "applications": candidate.get("applications", []),
                },
                displayName=f"{candidate.get('first_name')} {candidate.get('last_name')}",
            )

        # ── schedule_interview ────────────────────────────────────────────────
        elif action == "schedule_interview":
            if not req.candidate_id or not req.interviewer_email or not req.start_time or not req.end_time:
                return AgentTaskResponse(
                    status="failed",
                    error="candidate_id, interviewer_email, start_time, and end_time are all required.",
                )
            # POST /candidates/{candidate_id}/interviews
            # Mirrors JS payload exactly
            payload = {
                "interview": {
                    "starts_at": req.start_time,
                    "ends_at": req.end_time,
                    "interviewers": [{"email": req.interviewer_email}],
                }
            }
            res = session.post(
                f"{GREENHOUSE_API}/candidates/{req.candidate_id}/interviews",
                json=payload,
                timeout=15,
            )
            res.raise_for_status()
            result = res.json()
            return AgentTaskResponse(
                status="success",
                type="greenhouse_action",
                message=f"Interview scheduled for candidate {req.candidate_id} with {req.interviewer_email}.",
                data=result,
                displayName="Interview Scheduled",
            )

        else:
            return AgentTaskResponse(status="failed", error=f"Unknown action: {action}")

    except requests.exceptions.HTTPError as e:
        error_body = e.response.json() if e.response else str(e)
        error_msg = (error_body.get("error") if isinstance(error_body, dict) else str(error_body))
        logger.exception("Greenhouse API HTTP error")
        return AgentTaskResponse(
            status="failed",
            error=f"Greenhouse API request failed: {error_msg}",
        )
    except Exception as e:
        logger.exception("Greenhouse agent error")
        return AgentTaskResponse(status="failed", error=str(e))


@app.get("/health")
def health():
    return {"status": "healthy", "agent": "greenhouse-agent"}
