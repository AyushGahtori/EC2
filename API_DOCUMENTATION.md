# Agent APIs Documentation

This document describes the HTTP APIs for the Teams and Todo agents hosted on AWS EC2. The agents are now production-ready FastAPI servers with structured logging, CORS enabled for cross-origin communication, and systemd service management.

**Date:** March 27, 2026  
**Status:** Ready for EC2 Deployment

## Overview

Two independent FastAPI agents are deployed as HTTP microservices:
- **Teams Agent** (port 8100): Microsoft Teams/Email/Calendar operations
- **Todo Agent** (port 8200): Firestore-backed task management

Both agents run as systemd services on EC2, proxied through Nginx with HTTPS.

## Base URLs

- Teams Agent: `https://teams.yourdomain.com` (internally `http://127.0.0.1:8100`)
- Todo Agent: `https://todo.yourdomain.com` (internally `http://127.0.0.1:8200`)

Replace `yourdomain.com` with your actual EC2 domain.

## Authentication

Currently, no HTTP authentication is implemented (server-to-server calls only). For production multi-tenant scenarios, consider adding API keys or JWT tokens in the web app layer.

## Teams Agent API

### POST /teams/action

Execute a Teams-related action (call, message, meeting).

**Request Body:**
```json
{
  "action": "make_call" | "send_message" | "schedule_meeting",
  "contact": "person name or email",
  "message": "message text",
  "title": "meeting title",
  "attendees": ["email1", "email2"],
  "date": "YYYY-MM-DD",
  "time": "HH:MM",
  "duration": 60,
  "description": "agenda",
  "taskId": "optional-task-id",
  "userId": "user-id",
  "agentId": "teams-agent"
}
```

**Response:**
```json
{
  "status": "success" | "failed",
  "type": "teams_call" | "teams_message" | "teams_meeting",
  "url": "msteams://...",
  "displayName": "Name",
  "email": "email",
  "teamsUrl": "https://teams.microsoft.com/...",
  "outlookUrl": "https://outlook.office.com/...",
  "title": "Meeting Title",
  "date": "YYYY-MM-DD",
  "time": "HH:MM",
  "duration": 60,
  "resolvedAttendees": [{"name": "Name", "email": "email"}],
  "unresolvedAttendees": ["email"],
  "description": "agenda",
  "error": "error message",
  "flow": {}
}
```

### POST /email/action

Execute an email action.

**Request Body:** Similar to /teams/action, with email-specific fields.

### POST /calendar/action

Execute a calendar action.

**Request Body:** Similar to /teams/action, with calendar-specific fields.

### Auth Endpoints

- POST /auth/poll
- GET /auth/status
- POST /auth/logout

For Microsoft Graph authentication.

## Todo Agent API

### POST /todo/action

Execute a todo action.

**Request Body:**
```json
{
  "taskId": "task-id",
  "userId": "user-id",
  "agentId": "todo-agent",
  "action": "add_task" | "list_tasks" | "list_tasks_by_date" | "delete_task" | "mark_done",
  "title": "task title",
  "datetime": "YYYY-MM-DD HH:MM",
  "task_id": "task-id-to-delete",
  "status": "pending" | "completed"
}
```

**Response:**
```json
{
  "status": "success" | "failed",
  "type": "todo_action" | "todo_list",
  "error": "error message",
  "message": "response message",
  "tasks": [{"_id": "id", "title": "title", "datetime": "datetime", "status": "status"}],
  "displayName": "display name"
}
```

## Health Checks

- GET /health on both agents

## Web Application Integration

Your Next.js app (or Vercel) should make HTTPS POST requests to the agent URLs. Since agents are called server-to-server, place the calls in Next.js API routes (not in browser JavaScript).

### Next.js API Route Example

Create `/pages/api/actions/teams.ts` (or similar):

```typescript
import { NextApiRequest, NextApiResponse } from 'next';

export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse
) {
  if (req.method !== 'POST') return res.status(405).end();

  try {
    const agentResponse = await fetch('https://teams.yourdomain.com/teams/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: 'send_message',
        contact: req.body.contact,
        message: req.body.message,
        userId: req.body.userId,
        agentId: 'teams-agent',
        taskId: req.body.taskId
      })
    });

    if (!agentResponse.ok) {
      throw new Error(`Agent error: ${agentResponse.statusText}`);
    }

    const result = await agentResponse.json();
    return res.status(200).json(result);
  } catch (error) {
    console.error('Teams action error:', error);
    return res.status(500).json({ error: error.message });
  }
}
```

Then call from your frontend:

```javascript
const response = await fetch('/api/actions/teams', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    contact: 'john@example.com',
    message: 'Hello!',
    userId: 'user123'
  })
});
const result = await response.json();
```

**Key Points:**
- Call from Next.js API routes, not directly from browser
- Add userId/taskId to all requests for audit logging
- Implement retry logic with exponential backoff (agents may restart)
- Set timeouts (agents respond within 30s typically)

## Implementation Changes Made

### Code Modifications

**Teams Agent** (`agents/teams-agent/`):
- ✅ Removed Ollama LLM dependency (moved to web app layer)
- ✅ Updated CORS to allow all origins (`*`) for server-to-server calls
- ✅ Added structured JSON logging to all endpoints (`/teams/action`, `/email/action`, `/calendar/action`)
- ✅ Log format includes: timestamp, level, service, route, taskId, userId, agentId, status, latency_ms, error
- ✅ All routes now async with timing instrumentation

**Todo Agent** (`agents/todo-agent/`):
- ✅ Updated CORS to allow all origins
- ✅ Added structured JSON logging to `/todo/action` endpoint
- ✅ Updated Firebase service account key lookup path: checks `/app/.secrets/serviceAccountKey.json` first
- ✅ All routes now async with latency tracking
- ✅ Error handling returns proper status codes

### Infrastructure Files Created

| File | Purpose |
|------|---------|
| `systemd/teams-agent.service` | Systemd unit for Teams Agent, auto-restart on EC2 |
| `systemd/todo-agent.service` | Systemd unit for Todo Agent, auto-restart on EC2 |
| `nginx/sites-available/agents` | Nginx reverse proxy config for both agents with HTTPS support |
| `deploy.sh` | Automated deployment script (install deps, setup services, configure Nginx) |
| `API_DOCUMENTATION.md` | This comprehensive guide |

### Logging

Both agents output structured JSON logs to systemd journal:

```json
{
  "timestamp": "2026-03-27T12:34:56.789Z",
  "level": "INFO",
  "service": "teams-agent",
  "message": "Teams action completed",
  "route": "/teams/action",
  "taskId": "task-123",
  "userId": "user-456",
  "agentId": "teams-agent",
  "status": "success",
  "latency_ms": 1250,
  "error": null
}
```

View logs with:
```bash
journalctl -u teams-agent -f  # Follow teams-agent logs
journalctl -u todo-agent -f   # Follow todo-agent logs
journalctl -u teams-agent --output json  # JSON format
```

## Deployment Steps

### Step 1: Prepare EC2 Instance

```bash
# SSH into EC2
ssh -i your-key.pem ubuntu@your-ec2-ip

# Navigate to app directory
cd /home/ubuntu/app
```

### Step 2: Run Deployment Script

```bash
# Make script executable (if needed)
chmod +x deploy.sh

# Run as root or with sudo
sudo ./deploy.sh
```

This will:
- Install Python, pip, venv, Nginx, Certbot
- Create `/app/.secrets` directory
- Copy systemd service files to `/etc/systemd/system/`
- Create Python virtual environments
- Install dependencies from `requirements.txt`
- Configure Nginx reverse proxy
- Enable and start both services

### Step 3: Configure Firebase (Todo Agent)

```bash
# Place your Firebase service account JSON here
sudo cp serviceAccountKey.json /app/.secrets/
sudo chown ubuntu:ubuntu /app/.secrets/serviceAccountKey.json
sudo chmod 600 /app/.secrets/serviceAccountKey.json

# Verify todo-agent can access it
journalctl -u todo-agent -n 10
```

### Step 4: Set Up SSL/HTTPS with Certbot

```bash
# Run certbot for your domains
sudo certbot --nginx -d teams.yourdomain.com -d todo.yourdomain.com

# Auto-renew (already enabled by certbot)
sudo systemctl status certbot.timer
```

### Step 5: Update DNS

Point your domains to the EC2 instance:
```
teams.yourdomain.com  CNAME ec2-your-ip.compute-1.amazonaws.com
todo.yourdomain.com   CNAME ec2-your-ip.compute-1.amazonaws.com
```

### Step 6: Verify Services

```bash
# Check systemd services
sudo systemctl status teams-agent
sudo systemctl status todo-agent

# Test health endpoints
curl https://teams.yourdomain.com/health
curl https://todo.yourdomain.com/health

# View logs
journalctl -u teams-agent -f
journalctl -u todo-agent -f
```

## Configuration

### Environment Variables

Agents read environment variables from `.env` files in their directories.

**Teams Agent** (`agents/teams-agent/.env`):
```bash
GRAPH_CLIENT_ID=your-microsoft-client-id
GRAPH_CLIENT_SECRET=your-microsoft-client-secret
# Removed: OLLAMA_URL, OLLAMA_MODEL (now in web app)
```

**Todo Agent** (`agents/todo-agent/.env`):
```bash
FIREBASE_SERVICE_ACCOUNT_KEY=/app/.secrets/serviceAccountKey.json
```

### Nginx Customization

Edit `/etc/nginx/sites-available/agents` to:
- Change domain names
- Add rate limiting
- Add authentication headers
- Modify proxy timeouts

Then test and reload:
```bash
sudo nginx -t
sudo systemctl reload nginx
```

## Monitoring & Maintenance

### View Recent Logs

```bash
# Last 20 lines
journalctl -u teams-agent -n 20

# Last 1 hour
journalctl -u teams-agent --since "1 hour ago"

# Errors only
journalctl -u teams-agent -p err
```

### Restart Services

```bash
sudo systemctl restart teams-agent
sudo systemctl restart todo-agent

# Restart Nginx
sudo systemctl reload nginx
```

### Check Service Status

```bash
sudo systemctl is-active teams-agent
sudo systemctl is-active todo-agent
```

### Performance Monitoring

Check latency from logs:
```bash
journalctl -u teams-agent --output json | grep latency_ms
```

## Future Enhancements

### TODO (Before Multi-Instance Scaling)

1. **Token Persistence**: Move Microsoft auth tokens from in-memory to Redis/Firestore
   - Current: Lost on restart
   - Impact: Users must re-auth after agent restarts
   - Solution: Add Redis layer + token refresh logic

2. **API Authentication**: Add API keys or JWT
   - Current: No auth (server-to-server only)
   - Impact: Agents are only secure if called from trusted Next.js backend
   - Solution: Add bearer token validation middleware

3. **Rate Limiting**: Implement per-user rate limits
   - Current: No limits
   - Impact: Potential abuse
   - Solution: Redis-based rate limiter in Nginx or agents

4. **Database Migration**: Move from Firestore to PostgreSQL (if needed)
   - Impact: Cost optimization, multi-region support
   - Solution: Switch db/firestore.py to PostgreSQL driver

5. **Observability**: Add Prometheus metrics
   - Current: Just structured logs
   - Solution: Export metrics to CloudWatch or Datadog

## Troubleshooting

### Issue: Connection refused when calling agent
- Check if systemd service is running: `systemctl status teams-agent`
- Check if port is listening: `ss -tlnp | grep 8100`
- Check logs: `journalctl -u teams-agent -p err`

### Issue: 502 Bad Gateway from Nginx
- Verify upstream servers in Nginx config: `/etc/nginx/sites-available/agents`
- Test Nginx syntax: `nginx -t`
- Reload Nginx: `systemctl reload nginx`

### Issue: Todo agent won't start (Firebase error)
- Check if `/app/.secrets/serviceAccountKey.json` exists
- Verify permissions: `ls -l /app/.secrets/`
- Check Firebase credentials: `cat /app/.secrets/serviceAccountKey.json | head`

### Issue: High latency on requests
- Check agent logs for processing time: `journalctl -u teams-agent --output json | jq '.latency_ms'`
- Check if Microsoft Graph API is slow
- Check network connectivity to Firestore
- Monitor EC2 CPU/memory: `top`

## Summary

Your agents are now ready to be called from your Next.js web app via HTTPS. Deploy with `deploy.sh`, place Firebase credentials, and run Certbot for SSL. Monitor with systemd journal and structured JSON logs.