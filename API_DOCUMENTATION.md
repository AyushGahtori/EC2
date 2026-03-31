# Agent API Documentation

This document defines the HTTP contracts for EC2-hosted agent services.

## 1) Base URLs

Current deployed host (no domain yet):

- Shared base URL: `http://13.206.83.175`
- Teams health: `GET http://13.206.83.175/health`
- Todo health: `GET http://13.206.83.175/todo/health`
- Google health: `GET http://13.206.83.175/google/health`
- Notion health: `GET http://13.206.83.175/notion/health`
- Maps health: `GET http://13.206.83.175/maps/health`

Internal ports behind Nginx:

- Teams service: `127.0.0.1:8100`
- Todo service: `127.0.0.1:8200`
- Google service: `127.0.0.1:8300`
- Notion service: `127.0.0.1:8400`
- Maps service: `127.0.0.1:8500`

## 2) Request/Response Conventions

### Common Headers

- `Content-Type: application/json`
- Optional internal auth header (recommended): `X-Agent-Secret: <token>`

### Common Metadata Fields

Most requests should include:

- `taskId` (trace id)
- `userId` (tenant/user isolation)
- `agentId` (dispatcher target)

### Error Model

- Business/validation failures usually return HTTP `200` with `{"status":"failed","error":"..."}`.
- Unexpected server exceptions may return HTTP `500` in Teams endpoints.
- Todo endpoint currently returns HTTP `200` with failed status even for runtime exceptions.

## 3) Health Endpoints

### GET `/health` (Teams)

Response:

```json
{
  "status": "healthy",
  "agent": "teams-agent",
  "version": "1.0.0"
}
```

### GET `/health` (Todo)

Response:

```json
{
  "status": "healthy",
  "agent": "todo-agent"
}
```

Public route for Todo health through Nginx:

- `GET /todo/health` -> forwarded to Todo agent `/health`

## 4) Teams API

Endpoint: `POST /teams/action`

Supported `action` values:

- `make_call`
- `send_message`
- `schedule_meeting`

### Request Schema

```json
{
  "action": "make_call | send_message | schedule_meeting",
  "contact": "person name or email",
  "message": "message text or meeting description",
  "title": "meeting title",
  "attendees": ["email-or-name"],
  "date": "YYYY-MM-DD",
  "time": "HH:MM",
  "duration": 60,
  "description": "agenda",
  "taskId": "optional-trace-id",
  "userId": "user-id",
  "agentId": "teams-agent"
}
```

### Success Responses

`make_call`:

```json
{
  "status": "success",
  "type": "teams_call",
  "url": "msteams://teams.microsoft.com/l/call/0/0?users=user@example.com",
  "displayName": "User Name",
  "email": "user@example.com"
}
```

`send_message`:

```json
{
  "status": "success",
  "type": "teams_message",
  "url": "msteams://teams.microsoft.com/l/chat/0/0?users=user@example.com&message=Hello",
  "displayName": "User Name",
  "email": "user@example.com"
}
```

`schedule_meeting`:

```json
{
  "status": "success",
  "type": "teams_meeting",
  "teamsUrl": "https://teams.microsoft.com/l/meeting/new?...",
  "outlookUrl": "https://outlook.office.com/calendar/action/compose?...",
  "title": "Sprint Planning",
  "date": "2026-04-01",
  "time": "14:00",
  "duration": 60,
  "resolvedAttendees": [{"name": "A", "email": "a@corp.com"}],
  "unresolvedAttendees": [],
  "description": "Agenda"
}
```

### Device Auth Required Response

When Graph token is missing/expired:

```json
{
  "status": "action_required",
  "type": "device_auth",
  "flow": {
    "user_code": "...",
    "verification_uri": "https://microsoft.com/devicelogin"
  }
}
```

### Failure Response

```json
{
  "status": "failed",
  "error": "No contact specified."
}
```

## 5) Email API

Endpoint: `POST /email/action`

Supported `action` values:

- `read_inbox`
- `read_email`
- `summarize_email`
- `search_emails`
- `reply_to_email`
- `forward_email`
- `send_email`
- `mark_email`
- `move_email`
- `find_person_email`

### Request Examples

Read inbox:

```json
{
  "action": "read_inbox",
  "folder": "inbox",
  "limit": 10,
  "taskId": "t-1",
  "userId": "u-1",
  "agentId": "email-agent"
}
```

Send email:

```json
{
  "action": "send_email",
  "to": "john@example.com",
  "subject": "Status Update",
  "body": "Draft attached.",
  "cc": "lead@example.com"
}
```

### Response Notes

- Success shape depends on action (`emails`, `email`, `results`, or a simple message).
- Failure:

```json
{
  "status": "failed",
  "error": "message_id required"
}
```

## 6) Calendar API

Endpoint: `POST /calendar/action`

Supported `action` values:

- `get_calendar_events`
- `create_calendar_event`
- `check_conflicts`
- `delete_event`
- `find_person_email`

### Request Examples

Create event:

```json
{
  "action": "create_calendar_event",
  "title": "Design Review",
  "start_iso": "2026-04-01T10:00:00",
  "end_iso": "2026-04-01T11:00:00",
  "attendees": ["a@corp.com", "b@corp.com"],
  "description": "Review architecture changes"
}
```

Check conflicts:

```json
{
  "action": "check_conflicts",
  "start_iso": "2026-04-01T10:00:00",
  "end_iso": "2026-04-01T11:00:00"
}
```

## 7) Auth Endpoints (Graph Device Flow)

### POST `/auth/poll`

Checks active device flow state and returns one of:

- `{"status":"authenticated"}`
- `{"status":"pending"}`
- `{"status":"expired"}`

### GET `/auth/status`

Response:

```json
{"authenticated": true}
```

### POST `/auth/logout`

Response:

```json
{"status": "logged_out"}
```

## 8) Todo API

Endpoint: `POST /todo/action`

Supported `action` values:

- `add_task`
- `list_tasks`
- `list_tasks_by_date`
- `delete_task`
- `mark_done`

### Request Schema

```json
{
  "taskId": "trace-id",
  "userId": "user-id",
  "agentId": "todo-agent",
  "action": "add_task | list_tasks | list_tasks_by_date | delete_task | mark_done",
  "title": "task title",
  "datetime": "YYYY-MM-DD HH:MM or YYYY-MM-DD",
  "task_id": "existing task id",
  "status": "pending | done"
}
```

### Success Examples

Add task:

```json
{
  "status": "success",
  "type": "todo_action",
  "message": "Added task: Prepare report",
  "displayName": "Prepare report"
}
```

List tasks:

```json
{
  "status": "success",
  "type": "todo_list",
  "message": "Found 2 tasks.",
  "tasks": [
    {
      "_id": "uuid",
      "userId": "u-1",
      "title": "Prepare report",
      "datetime": "2026-04-01 09:00",
      "status": "pending"
    }
  ],
  "displayName": "View Tasks"
}
```

### Failure Examples

```json
{
  "status": "failed",
  "error": "Title is required"
}
```

```json
{
  "status": "failed",
  "error": "Multiple tasks match that title. Please clarify."
}
```

## 9) Google Workspace API

Endpoint: `POST /google/action`

Supported `agent_type` values:

- `gmail`
- `drive`
- `calendar`
- `meet`
- `tasks`
- `web_search`

### Request Schema

```json
{
  "taskId": "trace-id",
  "userId": "user-id",
  "agentId": "google-agent",
  "agent_type": "gmail | drive | calendar | meet | tasks | web_search",
  "action": "intent action string",
  "parameters": "plain-text parameters"
}
```

### Health & Auth Endpoints (via Nginx prefix)

- `GET /google/health`
- `GET /google/auth/login`
- `GET /google/auth/callback`
- `GET /google/auth/status`
- `POST /google/auth/logout`

## 10) Integration Guidance

- Keep browser calls pointed at backend only (Cloud Functions or Next.js API routes).
- Include `taskId`, `userId`, and `agentId` for traceability.
- Apply backend timeout and retry policy for transient upstream errors.
- Persist agent responses in task records for audit and UI history.

Recommended backend env values (current IP mode):

- `TEAMS_AGENT_BASE_URL=http://13.206.83.175`
- `TODO_AGENT_BASE_URL=http://13.206.83.175`
- `GOOGLE_AGENT_BASE_URL=http://13.206.83.175`

## 11) Change Management

Any endpoint contract change must include:

1. API doc update in this file.
2. Dispatcher/router update in backend (Cloud Function or API route).
3. Smoke test update for success + failure path.
