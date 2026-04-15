from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from firestore_store import (
    get_entity_by_field,
    list_entities,
    list_recent_snapshots,
    save_snapshot,
)
from schemas import LMSActionRequest, LMSActionResponse

DEFAULT_MODEL = (
    os.getenv("GEMINI_MODEL_FLASH")
    or os.getenv("GEMINI_MODEL")
    or os.getenv("GEMINI_MODEL_PRO")
    or "gemini-2.5-flash"
).strip()


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _to_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _to_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    for pattern in (r"```json\s*([\s\S]*?)\s*```", r"```\s*([\s\S]*?)\s*```", r"(\{[\s\S]*\})"):
        match = re.search(pattern, text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


async def _gemini_summary(prompt: str, fallback: str) -> str:
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        return fallback
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "topP": 0.9, "maxOutputTokens": 260},
    }
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{DEFAULT_MODEL}:generateContent",
                params={"key": api_key},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        text = "\n".join(str(part.get("text", "")) for part in parts if isinstance(part, dict)).strip()
        if not text:
            return fallback
        parsed = _extract_json(text)
        if isinstance(parsed.get("summary"), str):
            return parsed["summary"]
        return text[:500]
    except Exception:
        return fallback


def _require_user(req: LMSActionRequest) -> str:
    user_id = _clean(req.userId)
    if not user_id:
        raise ValueError("userId is required for lms-agent actions.")
    return user_id


def _normalize_status(value: Any) -> str:
    status = _clean(str(value)).lower()
    aliases = {
        "complete": "completed",
        "done": "completed",
        "pending": "pending",
        "in_progress": "in_progress",
        "progress": "in_progress",
        "failed": "failed",
    }
    return aliases.get(status, status or "pending")


def _group_progress_by_employee(progress_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in progress_rows:
        employee_id = _clean(str(row.get("employee_id") or row.get("employeeId") or ""))
        if not employee_id:
            continue
        out.setdefault(employee_id, []).append(row)
    return out


def _group_assignments_by_employee(assignment_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in assignment_rows:
        employee_id = _clean(str(row.get("employee_id") or row.get("employeeId") or ""))
        if not employee_id:
            continue
        out.setdefault(employee_id, []).append(row)
    return out


def _learner_rows(
    employees: list[dict[str, Any]],
    assignments: list[dict[str, Any]],
    progress: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    progress_by_employee = _group_progress_by_employee(progress)
    assignments_by_employee = _group_assignments_by_employee(assignments)

    rows: list[dict[str, Any]] = []
    for employee in employees:
        employee_id = _clean(str(employee.get("employee_id") or employee.get("employeeId") or employee.get("id") or ""))
        if not employee_id:
            continue
        employee_progress = progress_by_employee.get(employee_id, [])
        employee_assignments = assignments_by_employee.get(employee_id, [])

        completed = sum(1 for item in employee_progress if _normalize_status(item.get("status")) == "completed")
        enrollments = max(len(employee_assignments), len(employee_progress))
        trained_percentage = round((completed / enrollments) * 100, 1) if enrollments > 0 else 0.0

        rows.append(
            {
                "learnerId": employee_id,
                "fullName": _clean(str(employee.get("name") or employee.get("full_name") or employee.get("fullName") or employee_id)),
                "department": _clean(str(employee.get("department") or "Unassigned")),
                "skillLevel": _clean(str(employee.get("role") or employee.get("skillLevel") or "Intermediate")).title(),
                "dateJoined": _clean(str(employee.get("start_date") or employee.get("dateJoined") or ""))[:10] or "-",
                "role": _clean(str(employee.get("role") or "Learner")).replace("_", " ").title(),
                "trainedPercentage": trained_percentage,
                "enrollments": enrollments,
                "completed": completed,
            }
        )
    rows.sort(key=lambda item: item["trainedPercentage"], reverse=True)
    return rows


def _dashboard_overview(
    learners: list[dict[str, Any]],
    progress: list[dict[str, Any]],
) -> dict[str, Any]:
    passed = 0
    failed = 0
    in_progress = 0
    pending = 0
    for row in progress:
        status = _normalize_status(row.get("status"))
        if status == "completed":
            grade = _to_float(row.get("grade"), 100.0)
            if grade >= 60:
                passed += 1
            else:
                failed += 1
        elif status == "failed":
            failed += 1
        elif status == "in_progress":
            in_progress += 1
        else:
            pending += 1

    total_learners = len(learners)
    trained_count = sum(1 for learner in learners if _to_float(learner.get("trainedPercentage"), 0.0) >= 80.0)
    trained_percentage = round((trained_count / total_learners) * 100, 1) if total_learners else 0.0

    return {
        "trainedPercentage": trained_percentage,
        "passed": passed,
        "failed": failed,
        "inProgress": in_progress,
        "notStarted": pending,
        "totalLearners": total_learners,
        "completionDelta": round(trained_percentage / 10, 1) if trained_percentage else 0.0,
    }


def _build_segments(
    learners: list[dict[str, Any]],
    assignments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    department_counts: dict[str, int] = {}
    for learner in learners:
        department = _clean(str(learner.get("department") or "Unassigned"))
        department_counts[department] = department_counts.get(department, 0) + 1
    top_departments = sorted(department_counts.items(), key=lambda item: item[1], reverse=True)[:3]
    if not top_departments:
        return []

    days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    output: list[dict[str, Any]] = []
    for department, _count in top_departments:
        series = [0 for _ in days]
        for row in assignments:
            row_department = _clean(str(row.get("department") or ""))
            if row_department and row_department != department:
                continue
            date_value = (
                row.get("assigned_at")
                or row.get("assignedAt")
                or row.get("createdAtIso")
                or row.get("created_at")
            )
            parsed = _parse_iso(date_value)
            if parsed:
                series[parsed.weekday()] += 1
        output.append({"label": department, "series": series})
    return output


def _courses_catalog(
    courses: list[dict[str, Any]],
    assignments: list[dict[str, Any]],
    progress: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    enrollments_by_course: dict[str, int] = {}
    ratings_by_course: dict[str, list[float]] = {}
    for row in assignments:
        course_name = _clean(str(row.get("course_name") or row.get("courseName") or ""))
        if course_name:
            enrollments_by_course[course_name] = enrollments_by_course.get(course_name, 0) + 1
    for row in progress:
        course_name = _clean(str(row.get("course_name") or row.get("courseName") or ""))
        grade = _to_float(row.get("grade"), -1)
        if course_name and grade >= 0:
            ratings_by_course.setdefault(course_name, []).append(grade)

    out: list[dict[str, Any]] = []
    for row in courses:
        course_name = _clean(str(row.get("course_name") or row.get("courseName") or row.get("name") or ""))
        if not course_name:
            continue
        grades = ratings_by_course.get(course_name, [])
        avg_grade = (sum(grades) / len(grades)) if grades else 0.0
        rating = max(1, min(5, round(avg_grade / 20))) if avg_grade > 0 else 3
        out.append(
            {
                "courseId": _clean(str(row.get("moodle_course_id") or row.get("moodleCourseId") or row.get("id") or course_name)),
                "title": course_name,
                "enrolled": enrollments_by_course.get(course_name, 0),
                "difficulty": _clean(str(row.get("category") or row.get("difficulty") or "Intermediate")).title(),
                "status": _clean(str(row.get("status") or ("published" if row.get("active", True) else "draft"))).lower(),
                "rating": rating,
            }
        )
    out.sort(key=lambda item: item["enrolled"], reverse=True)
    return out


async def _moodle_sync_status(user_id: str) -> dict[str, Any]:
    moodle_url = _clean(os.getenv("MOODLE_URL"))
    moodle_token = _clean(os.getenv("MOODLE_TOKEN"))
    status = "disconnected"
    details = "Set MOODLE_URL and MOODLE_TOKEN for live sync checks."
    if moodle_url and moodle_token:
        try:
            endpoint = f"{moodle_url.rstrip('/')}/webservice/rest/server.php"
            params = {
                "wstoken": moodle_token,
                "wsfunction": "core_webservice_get_site_info",
                "moodlewsrestformat": "json",
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(endpoint, data=params)
                response.raise_for_status()
                payload = response.json()
            if isinstance(payload, dict) and payload.get("exception"):
                status = "degraded"
                details = _clean(str(payload.get("message"))) or "Moodle responded with a functional error."
            else:
                status = "healthy"
                details = "Connected to Moodle successfully."
        except Exception:
            status = "degraded"
            details = "Unable to reach Moodle endpoint with current credentials."

    api_call_rows = list_entities(user_id, "api_calls", limit=15)
    api_calls = []
    for row in api_call_rows:
        fn = _clean(str(row.get("function") or row.get("wsfunction") or row.get("name") or ""))
        if fn:
            api_calls.append(fn)

    return {
        "connectionHealth": status,
        "details": details,
        "checkedAt": _iso_now(),
        "recentApiCalls": api_calls[:8],
    }


def _build_compliance_tracks(
    tracks: list[dict[str, Any]],
    assignments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    out: list[dict[str, Any]] = []
    for row in tracks:
        track_name = _clean(str(row.get("track_name") or row.get("trackName") or row.get("name") or ""))
        if not track_name:
            continue
        courses = row.get("courses") if isinstance(row.get("courses"), list) else []
        timeline = {month: False for month in months}
        for assignment in assignments:
            course_name = _clean(str(assignment.get("course_name") or assignment.get("courseName") or ""))
            if courses and course_name not in [str(item) for item in courses]:
                continue
            parsed = _parse_iso(
                assignment.get("due_date")
                or assignment.get("dueDate")
                or assignment.get("assigned_at")
                or assignment.get("assignedAt")
                or assignment.get("createdAtIso")
            )
            if parsed:
                timeline[months[parsed.month - 1]] = True
        renewal_months = _to_int(row.get("renewal_months") or row.get("renewalMonths"), 12)
        out.append(
            {
                "name": track_name,
                "role": _clean(str(row.get("role") or "All")).replace("_", " ").title(),
                "timeline": timeline,
                "renewal": f"{renewal_months} months",
            }
        )
    return out


def _build_recent_assignments(
    assignments: list[dict[str, Any]],
    employees: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    employees_by_id: dict[str, str] = {}
    for employee in employees:
        employee_id = _clean(str(employee.get("employee_id") or employee.get("employeeId") or employee.get("id") or ""))
        if employee_id:
            employees_by_id[employee_id] = _clean(str(employee.get("name") or employee_id))

    def assignment_sort_key(row: dict[str, Any]) -> datetime:
        return (
            _parse_iso(row.get("assigned_at") or row.get("assignedAt") or row.get("createdAtIso"))
            or datetime.fromtimestamp(0, tz=timezone.utc)
        )

    sorted_rows = sorted(assignments, key=assignment_sort_key, reverse=True)
    out: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for row in sorted_rows[:8]:
        employee_id = _clean(str(row.get("employee_id") or row.get("employeeId") or ""))
        assigned_at = assignment_sort_key(row)
        elapsed_hours = max(0, int((now - assigned_at).total_seconds() // 3600))
        out.append(
            {
                "individual": employees_by_id.get(employee_id, employee_id or "Unknown"),
                "course": _clean(str(row.get("course_name") or row.get("courseName") or "Untitled Course")),
                "dueDate": f"{elapsed_hours} hours ago" if elapsed_hours < 48 else f"{elapsed_hours // 24} days ago",
            }
        )
    return out


def _missing_data_response(action: str, result_type: str) -> LMSActionResponse:
    return LMSActionResponse(
        status="needs_input",
        type=result_type,
        message=f"No LMS data found for action '{action}'.",
        summary="Sync your LMS records to Firebase first (employees, courses, assignments, progress, and compliance tracks).",
        result={
            "missingData": True,
            "suggestedInputs": ["employees", "courses", "assignments", "progress"],
        },
    )


async def _dashboard_action(user_id: str, req: LMSActionRequest) -> LMSActionResponse:
    employees = list_entities(user_id, "employees")
    assignments = list_entities(user_id, "training_assignments")
    progress = list_entities(user_id, "progress")
    if not employees:
        return _missing_data_response(req.action, "lms_dashboard_result")

    learners = _learner_rows(employees, assignments, progress)
    payload = {
        "view": "dashboard",
        "filters": {
            "dateRange": _clean(req.dateRange) or "Last 90 days",
            "department": _clean(req.department) or "All",
            "enrollmentType": _clean(req.enrollmentType) or "All",
            "courseType": _clean(req.courseType) or "All",
        },
        "overview": _dashboard_overview(learners, progress),
        "segments": _build_segments(learners, assignments),
        "learners": learners,
    }
    summary = await _gemini_summary(
        f"Write one concise executive LMS summary: {json.dumps(payload)}",
        "LMS dashboard is ready with learner progress and completion distribution.",
    )
    payload["summary"] = summary
    payload["snapshotId"] = save_snapshot(user_id, "dashboard", payload)
    return LMSActionResponse(
        status="success",
        type="lms_dashboard_result",
        message="Learner progress dashboard generated.",
        summary=summary,
        result=payload,
    )


async def _courses_action(user_id: str, req: LMSActionRequest) -> LMSActionResponse:
    courses = list_entities(user_id, "course_registry")
    assignments = list_entities(user_id, "training_assignments")
    progress = list_entities(user_id, "progress")
    if not courses:
        return _missing_data_response(req.action, "lms_courses_result")

    catalog = _courses_catalog(courses, assignments, progress)
    payload = {
        "view": "courses",
        "filters": {
            "department": _clean(req.department) or "All",
            "courseType": _clean(req.courseType) or "All",
            "dateRange": _clean(req.dateRange) or "Last 90 days",
        },
        "courses": catalog,
    }
    summary = await _gemini_summary(
        f"Write one concise summary for this LMS courses payload: {json.dumps(payload)}",
        "Course catalog summary is ready.",
    )
    payload["summary"] = summary
    payload["snapshotId"] = save_snapshot(user_id, "courses", payload)
    return LMSActionResponse(
        status="success",
        type="lms_courses_result",
        message="LMS courses catalog loaded.",
        summary=summary,
        result=payload,
    )


async def _learners_action(user_id: str, req: LMSActionRequest) -> LMSActionResponse:
    employees = list_entities(user_id, "employees")
    assignments = list_entities(user_id, "training_assignments")
    progress = list_entities(user_id, "progress")
    if not employees:
        return _missing_data_response(req.action, "lms_learners_directory_result")

    learners = _learner_rows(employees, assignments, progress)
    payload = {"view": "learners_directory", "learners": learners}
    summary = await _gemini_summary(
        f"Write one concise summary for this learners directory payload: {json.dumps(payload)}",
        "Learners directory is ready.",
    )
    payload["summary"] = summary
    payload["snapshotId"] = save_snapshot(user_id, "learners_directory", payload)
    return LMSActionResponse(
        status="success",
        type="lms_learners_directory_result",
        message="LMS learners directory loaded.",
        summary=summary,
        result=payload,
    )


async def _learner_detail_action(user_id: str, req: LMSActionRequest) -> LMSActionResponse:
    employees = list_entities(user_id, "employees")
    assignments = list_entities(user_id, "training_assignments")
    progress = list_entities(user_id, "progress")
    if not employees:
        return _missing_data_response(req.action, "lms_learner_detail_result")

    target_id = _clean(req.learnerId)
    target_name = _clean(req.learnerName)
    employee = None
    if target_id:
        employee = get_entity_by_field(user_id, "employees", "employee_id", target_id)
    if not employee and target_name:
        employee = get_entity_by_field(user_id, "employees", "name", target_name)
    if not employee:
        employee = employees[0] if employees else None
    if not employee:
        return _missing_data_response(req.action, "lms_learner_detail_result")

    employee_id = _clean(str(employee.get("employee_id") or employee.get("employeeId") or employee.get("id") or ""))
    employee_name = _clean(str(employee.get("name") or employee_id))
    employee_assignments = [row for row in assignments if _clean(str(row.get("employee_id") or row.get("employeeId") or "")) == employee_id]
    employee_progress = [row for row in progress if _clean(str(row.get("employee_id") or row.get("employeeId") or "")) == employee_id]

    completed = [row for row in employee_progress if _normalize_status(row.get("status")) == "completed"]
    grade_values = [_to_float(row.get("grade"), -1) for row in completed]
    grade_values = [value for value in grade_values if value >= 0]
    avg_grade = (sum(grade_values) / len(grade_values)) if grade_values else 0.0

    total_enrollments = max(len(employee_assignments), len(employee_progress), 1)
    completion_pct = round((len(completed) / total_enrollments) * 100, 1)
    assessments_passed = round(
        (
            len([row for row in completed if _to_float(row.get("grade"), 100.0) >= 60]) / max(len(completed), 1)
        )
        * 100,
        1,
    )

    payload = {
        "view": "learner_detail",
        "learner": {"id": employee_id, "name": employee_name, "subtitle": "learner progress"},
        "kpis": [
            {"label": "Skill Mastery", "value": round(avg_grade, 1)},
            {"label": "Course Completion", "value": completion_pct},
            {"label": "Assessments Passed", "value": assessments_passed},
        ],
        "segments": _build_segments(
            _learner_rows([employee], employee_assignments, employee_progress),
            employee_assignments,
        ),
        "milestones": [
            {"label": _clean(str(row.get("course_name") or row.get("courseName") or "Completed course")), "status": "Achieved"}
            for row in completed[:6]
        ],
        "upcomingDeadlines": [
            {
                "label": _clean(str(row.get("course_name") or row.get("courseName") or "Upcoming task")),
                "status": _clean(str(row.get("due_date") or row.get("dueDate") or "Upcoming Deadline"))[:10] or "Upcoming Deadline",
            }
            for row in employee_assignments[:6]
        ],
    }
    summary = await _gemini_summary(
        f"Write one concise manager summary for this learner detail payload: {json.dumps(payload)}",
        "Learner detail report is ready.",
    )
    payload["summary"] = summary
    payload["snapshotId"] = save_snapshot(user_id, "learner_detail", payload)
    return LMSActionResponse(
        status="success",
        type="lms_learner_detail_result",
        message="Learner detail report prepared.",
        summary=summary,
        result=payload,
    )


async def _assignments_sync_action(user_id: str, req: LMSActionRequest) -> LMSActionResponse:
    tracks = list_entities(user_id, "compliance_tracks")
    assignments = list_entities(user_id, "training_assignments")
    employees = list_entities(user_id, "employees")
    if not tracks and not assignments:
        return _missing_data_response(req.action, "lms_assignments_result")

    payload = {
        "view": "assignments_integrations",
        "complianceTracks": _build_compliance_tracks(tracks, assignments),
        "recentAssignments": _build_recent_assignments(assignments, employees),
        "moodleSyncStatus": await _moodle_sync_status(user_id),
    }
    summary = await _gemini_summary(
        f"Write one concise summary for this LMS assignments/integration payload: {json.dumps(payload)}",
        "Assignments and integration health view is ready.",
    )
    payload["summary"] = summary
    payload["snapshotId"] = save_snapshot(user_id, "assignments_integrations", payload)
    return LMSActionResponse(
        status="success",
        type="lms_assignments_result",
        message="Assignments and integrations view generated.",
        summary=summary,
        result=payload,
    )


def _infer_action_from_prompt(prompt: str) -> str:
    p = prompt.lower()
    if any(k in p for k in ["course catalog", "courses", "course list"]):
        return "courses_catalog"
    if any(k in p for k in ["learners directory", "learner directory", "users list"]):
        return "learners_directory"
    if any(k in p for k in ["learner detail", "skill mastery", "assessments passed"]):
        return "learner_detail"
    if any(k in p for k in ["moodle sync", "assignment", "compliance track", "integration"]):
        return "assignments_integrations"
    return "learner_progress_dashboard"


async def _snapshots_action(user_id: str) -> LMSActionResponse:
    snapshots = list_recent_snapshots(user_id, limit=10)
    return LMSActionResponse(
        status="success",
        type="lms_snapshots_result",
        message=f"Loaded {len(snapshots)} recent LMS snapshots.",
        summary="Recent LMS snapshots are available.",
        result={"view": "snapshots", "snapshots": snapshots},
    )


async def run_lms_action(req: LMSActionRequest) -> LMSActionResponse:
    try:
        user_id = _require_user(req)
        action = _clean(req.action).lower()

        if action in {"run_lms_agent", "run", "query"}:
            prompt = _clean(req.prompt) or _clean(req.context.get("prompt") if isinstance(req.context, dict) else None)
            if not prompt:
                return LMSActionResponse(
                    status="needs_input",
                    type="lms_dashboard_result",
                    message="I need a prompt to determine which LMS view to generate.",
                    summary="Share what you want to see: dashboard, courses, learners, learner detail, or assignments sync.",
                    result={"suggestedInputs": ["prompt"]},
                )
            action = _infer_action_from_prompt(prompt)

        if action in {"learner_progress_dashboard", "dashboard", "progress_dashboard"}:
            return await _dashboard_action(user_id, req)
        if action in {"courses_catalog", "courses"}:
            return await _courses_action(user_id, req)
        if action in {"learners_directory", "learners"}:
            return await _learners_action(user_id, req)
        if action in {"learner_detail", "learner_report"}:
            return await _learner_detail_action(user_id, req)
        if action in {"assignments_integrations", "integrations", "assignments"}:
            return await _assignments_sync_action(user_id, req)
        if action in {"list_snapshots", "recent_snapshots"}:
            return await _snapshots_action(user_id)

        return LMSActionResponse(
            status="failed",
            type="lms_dashboard_result",
            message=f"Unsupported action: {req.action}",
            summary="Requested LMS action is not available.",
            error=f"Unknown action: {req.action}",
        )
    except ValueError as exc:
        return LMSActionResponse(
            status="needs_input",
            type="lms_dashboard_result",
            message=str(exc),
            summary="LMS agent requires missing input.",
            result={"suggestedInputs": ["userId"]},
            error=str(exc),
        )
    except Exception:
        return LMSActionResponse(
            status="failed",
            type="lms_dashboard_result",
            message="LMS agent failed to process this request.",
            summary="Please retry with a specific LMS view request.",
            error="lms_agent_failed",
        )
