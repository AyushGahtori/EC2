"""
Google Tasks Agent
Handles Google Tasks operations: create, list, and mark complete
"""

import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

from agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

TASKS_BASE_URL = "https://tasks.googleapis.com/tasks/v1"


class TasksAgent(BaseAgent):
    """Agent for Google Tasks operations."""

    async def handle(self, user_message: str, context: Dict[str, Any]) -> Dict[str, Any]:
        action = await self._determine_action(user_message, context)
        logger.info("[tasks] action: %s", action)

        if action == "create":
            return await self.create_task(user_message, context)
        if action == "complete":
            return await self.complete_task(user_message, context)
        return await self.list_tasks(user_message, context)

    async def _determine_action(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        params = await self.extract_parameters(
            user_message=user_message,
            schema_description='action: one of "create", "list", "complete"',
            example_output='{"action": "list"}',
            context=context,
        )
        return params.get("action", "list")

    async def create_task(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        params = await self.extract_parameters(
            user_message=user_message,
            schema_description="""
- title: task title
- notes: optional task notes
- due: optional RFC3339 due datetime or YYYY-MM-DD date
- task_list: optional Google Task list name
            """,
            example_output='''{
  "title": "Submit quarterly report",
  "notes": "Send the finished PDF to finance",
  "due": "2026-03-29",
  "task_list": null
}''',
            context=context,
        )

        title = (params.get("title") or "").strip()
        if not title:
            return self.failure(
                error="VALIDATION_ERROR",
                message="Please tell me the Google Task title to create.",
            )

        task_list, error_response = await self._resolve_task_list(params.get("task_list"))
        if error_response:
            return error_response

        payload: Dict[str, Any] = {"title": title}
        if params.get("notes"):
            payload["notes"] = params["notes"]

        due = self._normalize_due(params.get("due"))
        if due:
            payload["due"] = due

        try:
            response = await self.request_google_api(
                "POST",
                f"{TASKS_BASE_URL}/lists/{task_list['id']}/tasks",
                json=payload,
            )
        except Exception as exc:
            return self.handle_google_exception(
                "Google Tasks",
                exc,
                data={"task": payload, "task_list": task_list},
            )

        if response.status_code in (200, 201):
            task = response.json()
            return self.success(
                summary=f"Created Google Task '{task.get('title', title)}' in '{task_list['title']}'.",
                data={"task": task, "task_list": task_list},
            )

        return self.handle_google_api_error(
            "Google Tasks",
            response,
            data={"task": payload, "task_list": task_list},
        )

    async def list_tasks(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        params = await self.extract_parameters(
            user_message=user_message,
            schema_description="""
- task_list: optional Google Task list name
- max_results: maximum number of tasks to list
- show_completed: true if completed tasks should be included
            """,
            example_output='{"task_list": null, "max_results": 10, "show_completed": false}',
            context=context,
        )

        task_list, error_response = await self._resolve_task_list(params.get("task_list"))
        if error_response:
            return error_response

        max_results = params.get("max_results") or 10
        try:
            max_results = max(1, min(int(max_results), 25))
        except (TypeError, ValueError):
            max_results = 10

        show_completed = bool(params.get("show_completed", False))

        try:
            response = await self.request_google_api(
                "GET",
                f"{TASKS_BASE_URL}/lists/{task_list['id']}/tasks",
                params={
                    "maxResults": max_results,
                    "showCompleted": show_completed,
                    "showHidden": False,
                },
                retry_on_failure=True,
            )
        except Exception as exc:
            return self.handle_google_exception(
                "Google Tasks",
                exc,
                data={"task_list": task_list},
            )

        if response.status_code == 200:
            tasks = response.json().get("items", [])
            if not tasks:
                return self.success(
                    summary=f"No tasks found in '{task_list['title']}'.",
                    data={"tasks": [], "task_list": task_list},
                )

            summary_lines = []
            for task in tasks:
                status = "done" if task.get("status") == "completed" else "open"
                summary_lines.append(f"- [{status}] {task.get('title', 'Untitled task')}")

            return self.success(
                summary=f"Tasks in '{task_list['title']}':\n" + "\n".join(summary_lines),
                data={"tasks": tasks, "task_list": task_list},
            )

        return self.handle_google_api_error(
            "Google Tasks",
            response,
            data={"task_list": task_list},
        )

    async def complete_task(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        params = await self.extract_parameters(
            user_message=user_message,
            schema_description="""
- title: title or partial title of the task to mark complete
- task_list: optional Google Task list name
            """,
            example_output='{"title": "Submit quarterly report", "task_list": null}',
            context=context,
        )

        title = (params.get("title") or "").strip()
        if not title:
            return self.failure(
                error="VALIDATION_ERROR",
                message="Please tell me which Google Task to mark complete.",
            )

        task_list, error_response = await self._resolve_task_list(params.get("task_list"))
        if error_response:
            return error_response

        task, lookup_error = await self._find_task_by_title(task_list["id"], title)
        if lookup_error:
            return lookup_error
        if not task:
            return self.success(
                summary=f"No Google Task found matching '{title}' in '{task_list['title']}'.",
                data={"task": None, "task_list": task_list},
            )

        if task.get("status") == "completed":
            return self.success(
                summary=f"Google Task '{task.get('title', title)}' is already completed.",
                data={"task": task, "task_list": task_list},
            )

        payload = {
            "title": task.get("title", title),
            "notes": task.get("notes", ""),
            "status": "completed",
            "completed": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        }
        if task.get("due"):
            payload["due"] = task["due"]

        try:
            response = await self.request_google_api(
                "PATCH",
                f"{TASKS_BASE_URL}/lists/{task_list['id']}/tasks/{task['id']}",
                json=payload,
            )
        except Exception as exc:
            return self.handle_google_exception(
                "Google Tasks",
                exc,
                data={"task": task, "task_list": task_list},
            )

        if response.status_code == 200:
            updated_task = response.json()
            return self.success(
                summary=f"Marked Google Task '{updated_task.get('title', title)}' as completed.",
                data={"task": updated_task, "task_list": task_list},
            )

        return self.handle_google_api_error(
            "Google Tasks",
            response,
            data={"task": task, "task_list": task_list},
        )

    async def _resolve_task_list(
        self,
        requested_task_list: Optional[str] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        try:
            response = await self.request_google_api(
                "GET",
                f"{TASKS_BASE_URL}/users/@me/lists",
                params={"maxResults": 20},
                retry_on_failure=True,
            )
        except Exception as exc:
            return None, self.handle_google_exception("Google Tasks", exc)

        if response.status_code != 200:
            return None, self.handle_google_api_error("Google Tasks", response)

        task_lists = response.json().get("items", [])
        if not task_lists:
            return None, self.failure(
                error="TASK_LIST_NOT_FOUND",
                message="No Google Task lists are available for this account.",
            )

        requested_name = (requested_task_list or "").strip().lower()
        if requested_name:
            for task_list in task_lists:
                if requested_name == task_list.get("title", "").strip().lower():
                    return task_list, None
            for task_list in task_lists:
                if requested_name in task_list.get("title", "").strip().lower():
                    return task_list, None

            return None, self.failure(
                error="TASK_LIST_NOT_FOUND",
                message=f"Could not find a Google Task list matching '{requested_task_list}'.",
            )

        return task_lists[0], None

    async def _find_task_by_title(
        self,
        task_list_id: str,
        title: str,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        try:
            response = await self.request_google_api(
                "GET",
                f"{TASKS_BASE_URL}/lists/{task_list_id}/tasks",
                params={
                    "showCompleted": True,
                    "showHidden": True,
                    "maxResults": 100,
                },
                retry_on_failure=True,
            )
        except Exception as exc:
            return None, self.handle_google_exception("Google Tasks", exc)

        if response.status_code != 200:
            return None, self.handle_google_api_error("Google Tasks", response)

        tasks = response.json().get("items", [])
        if not tasks:
            return None, None

        normalized_title = title.lower()
        exact_matches: List[Dict[str, Any]] = []
        partial_matches: List[Dict[str, Any]] = []
        for task in tasks:
            task_title = task.get("title", "")
            lower_task_title = task_title.lower()
            if lower_task_title == normalized_title:
                exact_matches.append(task)
            elif normalized_title in lower_task_title:
                partial_matches.append(task)

        if exact_matches:
            return exact_matches[0], None
        if partial_matches:
            return partial_matches[0], None
        return None, None

    def _normalize_due(self, due: Optional[str]) -> Optional[str]:
        if not due:
            return None

        due_value = str(due).strip()
        if not due_value:
            return None

        if "T" in due_value:
            if due_value.endswith("Z") or "+" in due_value:
                return due_value
            return due_value + "Z"

        return f"{due_value}T00:00:00.000Z"
