"""
api/server.py — FastAPI REST API for the Todo AI Agent.
Mapped to executeAgentTask via POST /todo/action
"""
from __future__ import annotations

import logging
import os
import json
from datetime import datetime
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from db.firestore import (
    add_task, delete_task, get_tasks,
    mark_done, update_task, get_tasks_by_date
)

logger = logging.getLogger(__name__)

# Setup structured JSON logging
logging.basicConfig(level=logging.INFO)

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "service": "todo-agent",
            "message": record.getMessage(),
            "route": getattr(record, 'route', None),
            "taskId": getattr(record, 'taskId', None),
            "userId": getattr(record, 'userId', None),
            "agentId": getattr(record, 'agentId', None),
            "status": getattr(record, 'status', None),
            "latency_ms": getattr(record, 'latency_ms', None),
            "error": getattr(record, 'error', None),
        }
        return json.dumps(log_entry)

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

# ── Pydantic schemas ───────────────────────────────────────────────────────────

class AgentTaskRequest(BaseModel):
    taskId: str
    userId: str
    agentId: str
    action: str
    # Parameters that the Next.js parent LLM will extract:
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

# ── Routes ─────────────────────────────────────────────────────────────────────

@app.post("/todo/action", response_model=AgentTaskResponse)
async def execute_todo_action(req: AgentTaskRequest, request: Request) -> AgentTaskResponse:
    start_time = time.time()
    extra = {
        'route': '/todo/action',
        'taskId': req.taskId,
        'userId': req.userId,
        'agentId': req.agentId,
    }
    logger.info("Todo action request received", extra=extra)
    user_id = req.userId
    action = req.action

    try:
        if action == "add_task":
            if not req.title:
                result = AgentTaskResponse(status="failed", error="Title is required")
                latency = int((time.time() - start_time) * 1000)
                extra.update({'status': 'failed', 'latency_ms': latency, 'error': 'Title is required'})
                logger.info("Todo action completed", extra=extra)
                return result
                latency = int((time.time() - start_time) * 1000)
                extra.update({'status': 'failed', 'latency_ms': latency, 'error': 'Title is required'})
                logger.info("Todo action completed", extra=extra)
                return result
            
            task_dict = {"title": req.title}
            if req.datetime:
                task_dict["datetime"] = req.datetime
                
            tid = add_task(user_id, task_dict)
            result = AgentTaskResponse(
                status="success", 
                type="todo_action",
                message=f"Added task: {req.title}",
                displayName=req.title
            )
            latency = int((time.time() - start_time) * 1000)
            extra.update({'status': 'success', 'latency_ms': latency})
            logger.info("Todo action completed", extra=extra)
            return result
            latency = int((time.time() - start_time) * 1000)
            extra.update({'status': 'success', 'latency_ms': latency})
            logger.info("Todo action completed", extra=extra)
            return result
            
        elif action == "list_tasks":
            # The parent LLM might ask to list pending tasks or all tasks
            tasks = get_tasks(user_id, status=req.status)
            result = AgentTaskResponse(
                status="success", 
                type="todo_list",
                tasks=tasks,
                message=f"Found {len(tasks)} tasks.",
                displayName="View Tasks"
            )
            latency = int((time.time() - start_time) * 1000)
            extra.update({'status': 'success', 'latency_ms': latency})
            logger.info("Todo action completed", extra=extra)
            return result
            latency = int((time.time() - start_time) * 1000)
            extra.update({'status': 'success', 'latency_ms': latency})
            logger.info("Todo action completed", extra=extra)
            return result

        elif action == "list_tasks_by_date":
            if not req.datetime:
                return AgentTaskResponse(status="failed", error="Date string is required in YYYY-MM-DD format")
            tasks = get_tasks_by_date(user_id, req.datetime)
            return AgentTaskResponse(
                status="success", 
                type="todo_list",
                tasks=tasks,
                message=f"Found {len(tasks)} tasks for {req.datetime}.",
                displayName=f"Tasks for {req.datetime}"
            )
            
        elif action == "delete_task":
            tid = req.task_id
            if not tid and req.title:
                all_t = get_tasks(user_id)
                found = [t for t in all_t if (t.get('title') or '').lower() == req.title.lower() or req.title.lower() in (t.get('title') or '').lower()]
                if len(found) == 1:
                    tid = found[0]["_id"]
                elif len(found) > 1:
                    return AgentTaskResponse(status="failed", error="Multiple tasks match that title. Please clarify.")

            if not tid:
                return AgentTaskResponse(status="failed", error="Could not find a unique task to delete.")
                
            ok = delete_task(user_id, tid)
            if ok:
                return AgentTaskResponse(status="success", type="todo_action", message="Task deleted", displayName="Deleted Task")
            return AgentTaskResponse(status="failed", error="Task not found or permission denied")
            
        elif action == "mark_done":
            tid = req.task_id
            if not tid and req.title:
                all_t = get_tasks(user_id, status="pending")
                found = [t for t in all_t if (t.get('title') or '').lower() == req.title.lower() or req.title.lower() in (t.get('title') or '').lower()]
                if len(found) == 1:
                    tid = found[0]["_id"]
                elif len(found) > 1:
                    return AgentTaskResponse(status="failed", error="Multiple tasks match that title. Please clarify.")

            if not tid:
                return AgentTaskResponse(status="failed", error="Could not find a unique pending task to complete.")
                
            ok = mark_done(user_id, tid)
            if ok:
                return AgentTaskResponse(status="success", type="todo_action", message="Task marked as complete", displayName="Completed Task")
            return AgentTaskResponse(status="failed", error="Task not found or permission denied")
            
        else:
            result = AgentTaskResponse(status="failed", error=f"Unknown action: {action}")
            latency = int((time.time() - start_time) * 1000)
            extra.update({'status': 'failed', 'latency_ms': latency, 'error': f"Unknown action: {action}"})
            logger.error("Todo action failed", extra=extra)
            return result
            
    except Exception as e:
        latency = int((time.time() - start_time) * 1000)
        extra.update({'status': 'failed', 'latency_ms': latency, 'error': str(e)})
        logger.error("Todo action failed", extra=extra)
        return AgentTaskResponse(status="failed", error=str(e))

@app.get("/health", tags=["System"])
def health():
    return {
        "status": "healthy",
        "agent": "todo-agent"
    }
