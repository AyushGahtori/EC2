"""
api/server.py - FastAPI REST API for the Todo AI Agent.
Mapped to executeAgentTask via POST /todo/action
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from db.firestore import add_task, delete_task, get_tasks, get_tasks_by_date, mark_done

logger = logging.getLogger("todo-agent")
logger.setLevel(logging.INFO)


class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "service": "todo-agent",
            "message": record.getMessage(),
            "route": getattr(record, "route", None),
            "taskId": getattr(record, "taskId", None),
            "userId": getattr(record, "userId", None),
            "agentId": getattr(record, "agentId", None),
            "status": getattr(record, "status", None),
            "latency_ms": getattr(record, "latency_ms", None),
            "error": getattr(record, "error", None),
        }
        return json.dumps(log_entry)


if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
logger.propagate = False

app = FastAPI(
    title="Todo AI Agent API",
    description="AI-powered task manager for SnitchX.",
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AgentTaskRequest(BaseModel):
    taskId: str
    userId: str
    agentId: str
    action: str
    title: str | None = None
    datetime: str | None = None
    task_id: str | None = None
    status: str | None = None


class AgentTaskResponse(BaseModel):
    status: str
    type: str | None = None
    error: str | None = None
    message: str | None = None
    tasks: list[dict] | None = None
    displayName: str | None = None


def _log_completion(
    base_extra: dict,
    start_time: float,
    status: str,
    error: str | None = None,
) -> None:
    latency = int((time.time() - start_time) * 1000)
    extra = {
        **base_extra,
        "status": status,
        "latency_ms": latency,
    }
    if error:
        extra["error"] = error

    if status == "failed":
        logger.error("Todo action failed", extra=extra)
    else:
        logger.info("Todo action completed", extra=extra)


@app.post("/todo/action", response_model=AgentTaskResponse)
async def execute_todo_action(req: AgentTaskRequest, request: Request) -> AgentTaskResponse:
    start_time = time.time()
    base_extra = {
        "route": "/todo/action",
        "taskId": req.taskId,
        "userId": req.userId,
        "agentId": req.agentId,
    }
    logger.info("Todo action request received", extra=base_extra)

    user_id = req.userId
    action = req.action

    try:
        if action == "add_task":
            if not req.title:
                result = AgentTaskResponse(status="failed", error="Title is required")
            else:
                task_dict = {"title": req.title}
                if req.datetime:
                    task_dict["datetime"] = req.datetime
                add_task(user_id, task_dict)
                result = AgentTaskResponse(
                    status="success",
                    type="todo_action",
                    message=f"Added task: {req.title}",
                    displayName=req.title,
                )

        elif action == "list_tasks":
            tasks = get_tasks(user_id, status=req.status)
            result = AgentTaskResponse(
                status="success",
                type="todo_list",
                tasks=tasks,
                message=f"Found {len(tasks)} tasks.",
                displayName="View Tasks",
            )

        elif action == "list_tasks_by_date":
            if not req.datetime:
                result = AgentTaskResponse(
                    status="failed",
                    error="Date string is required in YYYY-MM-DD format",
                )
            else:
                tasks = get_tasks_by_date(user_id, req.datetime)
                result = AgentTaskResponse(
                    status="success",
                    type="todo_list",
                    tasks=tasks,
                    message=f"Found {len(tasks)} tasks for {req.datetime}.",
                    displayName=f"Tasks for {req.datetime}",
                )

        elif action == "delete_task":
            task_id = req.task_id
            if not task_id and req.title:
                all_tasks = get_tasks(user_id)
                found = [
                    task
                    for task in all_tasks
                    if (task.get("title") or "").lower() == req.title.lower()
                    or req.title.lower() in (task.get("title") or "").lower()
                ]
                if len(found) == 1:
                    task_id = found[0]["_id"]
                elif len(found) > 1:
                    result = AgentTaskResponse(
                        status="failed",
                        error="Multiple tasks match that title. Please clarify.",
                    )
                    _log_completion(base_extra, start_time, result.status, result.error)
                    return result

            if not task_id:
                result = AgentTaskResponse(
                    status="failed",
                    error="Could not find a unique task to delete.",
                )
            else:
                deleted = delete_task(user_id, task_id)
                if deleted:
                    result = AgentTaskResponse(
                        status="success",
                        type="todo_action",
                        message="Task deleted",
                        displayName="Deleted Task",
                    )
                else:
                    result = AgentTaskResponse(
                        status="failed",
                        error="Task not found or permission denied",
                    )

        elif action == "mark_done":
            task_id = req.task_id
            if not task_id and req.title:
                all_tasks = get_tasks(user_id, status="pending")
                found = [
                    task
                    for task in all_tasks
                    if (task.get("title") or "").lower() == req.title.lower()
                    or req.title.lower() in (task.get("title") or "").lower()
                ]
                if len(found) == 1:
                    task_id = found[0]["_id"]
                elif len(found) > 1:
                    result = AgentTaskResponse(
                        status="failed",
                        error="Multiple tasks match that title. Please clarify.",
                    )
                    _log_completion(base_extra, start_time, result.status, result.error)
                    return result

            if not task_id:
                result = AgentTaskResponse(
                    status="failed",
                    error="Could not find a unique pending task to complete.",
                )
            else:
                completed = mark_done(user_id, task_id)
                if completed:
                    result = AgentTaskResponse(
                        status="success",
                        type="todo_action",
                        message="Task marked as complete",
                        displayName="Completed Task",
                    )
                else:
                    result = AgentTaskResponse(
                        status="failed",
                        error="Task not found or permission denied",
                    )

        else:
            result = AgentTaskResponse(status="failed", error=f"Unknown action: {action}")

        _log_completion(base_extra, start_time, result.status, result.error)
        return result

    except Exception as exc:
        _log_completion(base_extra, start_time, "failed", str(exc))
        return AgentTaskResponse(status="failed", error=str(exc))


@app.get("/health", tags=["System"])
def health():
    return {
        "status": "healthy",
        "agent": "todo-agent",
    }
