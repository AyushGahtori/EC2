"""
api/server.py — Day Planner Agent
Converted from: marketplace copy/backend/tools/day_planner.js

Auth: none (internal — uses Firebase Firestore via service account)
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta

import firebase_admin
from dotenv import load_dotenv
from fastapi import FastAPI
from ec2_shared.api_security import apply_api_security
from firebase_admin import credentials, firestore
from pydantic import BaseModel, ConfigDict

load_dotenv()
logger = logging.getLogger(__name__)

# ── Firebase init ─────────────────────────────────────────────────────────────
_KEY_PATH = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY", "/home/ubuntu/app/.secrets/serviceAccountKey.json")
if not firebase_admin._apps:
    cred = credentials.Certificate(_KEY_PATH)
    firebase_admin.initialize_app(cred)

db = firestore.client()

app = FastAPI(title="Day Planner Agent API", version="1.0.0")
apply_api_security(app)


class AgentTaskRequest(BaseModel):
    taskId: str
    userId: str
    agentId: str
    action: str
    # Action-specific fields (mirrors JS args object)
    date: str | None = None
    time_: str | None = None          # 'time' is a Python keyword — sent as 'time' from client
    title: str | None = None
    description: str | None = None
    priority: str | None = None        # 'high' | 'medium' | 'low'
    duration: int | None = None
    startDate: str | None = None
    model_config = ConfigDict(extra="allow")


class AgentTaskResponse(BaseModel):
    status: str
    type: str | None = None
    error: str | None = None
    message: str | None = None
    tasks: list[dict] | None = None
    days: list[dict] | None = None
    displayName: str | None = None


def _get_today() -> str:
    return date.today().isoformat()


def _get_monday() -> str:
    """Return the ISO date of the current week's Monday."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return monday.isoformat()


def _plans_ref(user_id: str):
    return db.collection("users").document(user_id).collection("dayPlans")


@app.post("/dayplanner/action", response_model=AgentTaskResponse)
def execute_day_planner_action(req: AgentTaskRequest) -> AgentTaskResponse:
    """
    Mirrors the execute() function in day_planner.js.
    Reads/writes to Firestore users/{userId}/dayPlans/{date}.
    """
    action = req.action
    user_id = req.userId

    # Pull 'time' from extra fields since it's a keyword clash
    raw_time = req.model_extra.get("time") if req.model_extra else None
    plan_time = req.time_ or raw_time

    try:
        plans_ref = _plans_ref(user_id)

        # ── get_daily_plan ────────────────────────────────────────────────────
        if action == "get_daily_plan":
            target_date = req.date or _get_today()
            doc = plans_ref.document(target_date).get()

            if not doc.exists:
                return AgentTaskResponse(
                    status="success",
                    type="day_planner_list",
                    message=f"No plan items for {target_date}. Would you like me to help you plan your day?",
                    tasks=[],
                    displayName=f"Plan for {target_date}",
                )

            data = doc.to_dict()
            items = sorted(data.get("items", []), key=lambda x: x.get("time", ""))
            return AgentTaskResponse(
                status="success",
                type="day_planner_list",
                message=f"You have {len(items)} item(s) planned for {target_date}.",
                tasks=items,
                displayName=f"Plan for {target_date}",
            )

        # ── add_to_plan ───────────────────────────────────────────────────────
        elif action == "add_to_plan":
            if not req.title:
                return AgentTaskResponse(status="failed", error="title is required.")

            target_date = req.date or _get_today()
            new_item = {
                "id": f"item_{int(time.time() * 1000)}",
                "title": req.title,
                "description": req.description or "",
                "time": plan_time or "",
                "priority": req.priority or "medium",
                "duration": req.duration or 30,
                "completed": False,
                "createdAt": datetime.utcnow().isoformat(),
            }
            plan_doc = plans_ref.document(target_date)
            existing = plan_doc.get()

            if existing.exists:
                # Append to existing items array (mirrors arrayUnion in JS)
                current_items = existing.to_dict().get("items", [])
                current_items.append(new_item)
                plan_doc.update({"items": current_items})
            else:
                plan_doc.set({"items": [new_item], "date": target_date})

            time_str = f" at {plan_time}" if plan_time else ""
            return AgentTaskResponse(
                status="success",
                type="day_planner_action",
                message=f'Added "{req.title}" to your plan for {target_date}{time_str}.',
                displayName=req.title,
            )

        # ── get_weekly_overview ───────────────────────────────────────────────
        elif action == "get_weekly_overview":
            start_date_str = req.startDate or _get_monday()
            start = datetime.fromisoformat(start_date_str).date()
            days = []

            for i in range(7):
                current = start + timedelta(days=i)
                date_str = current.isoformat()
                day_name = current.strftime("%A")

                doc = plans_ref.document(date_str).get()
                items = doc.to_dict().get("items", []) if doc.exists else []

                days.append({
                    "date": date_str,
                    "day": day_name,
                    "items": [
                        {
                            "title": it.get("title"),
                            "time": it.get("time"),
                            "priority": it.get("priority"),
                            "completed": it.get("completed"),
                        }
                        for it in items
                    ],
                    "count": len(items),
                })

            total = sum(d["count"] for d in days)
            return AgentTaskResponse(
                status="success",
                type="day_planner_week",
                message=f"You have {total} item(s) planned across the week.",
                days=days,
                displayName="Weekly Plan",
            )

        else:
            return AgentTaskResponse(status="failed", error=f"Unknown action: {action}")

    except Exception as e:
        logger.exception("Day Planner agent error")
        return AgentTaskResponse(status="failed", error=str(e))


@app.get("/health")
def health():
    return {"status": "healthy", "agent": "day-planner-agent"}
