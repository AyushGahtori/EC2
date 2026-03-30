# EC2 Deployment Runbook

This runbook is the source-of-truth for deploying and operating agent services on AWS EC2.

## 1) Architecture

Request flow:

1. Browser / app client sends request to backend layer (Firebase Cloud Functions or Next.js API route).
2. Backend validates user/session and maps action to an agent endpoint.
3. Backend calls EC2 agent URL through HTTPS (Nginx).
4. Nginx routes request to local FastAPI service (`teams-agent` or `todo-agent`).
5. Agent response is returned to backend, then to browser.

Current runtime:

- `teams-agent` FastAPI service on `127.0.0.1:8100`
- `todo-agent` FastAPI service on `127.0.0.1:8200`
- `google-agent` FastAPI service on `127.0.0.1:8300`
- Nginx on `:80` (and `:443` after Certbot)
- systemd for process supervision and restart

## 2) Completed In This Iteration

The following updates were applied in this repository:

### Repo Separation / Git Hygiene

- Added ignore rule in SaaS root repo: `E:\SaaS-ai\.gitignore` now ignores `ai-everyone/EC2/`.
- Added ignore rule in SaaS app repo: `E:\SaaS-ai\ai-everyone\.gitignore` now ignores `/EC2/`.
- Expanded `E:\SaaS-ai\ai-everyone\EC2\.gitignore` to prevent accidental web-app file mixing (`/src/`, `/public/`, `/functions/`, `package.json`, etc.) and added stronger local-secret/runtime ignores.

### Teams Agent Fixes

- Fixed duplicate import in `agents/teams-agent/server.py` (`Request` imported once).
- Fixed invalid CORS combination by setting `allow_credentials=False` with wildcard origins.
- Hardened logger setup to avoid duplicate handlers on reload/restart.

### Todo Agent Fixes

- Refactored `agents/todo-agent/api/server.py` to remove duplicated/unreachable code branches.
- Standardized success/failure structured logging across all action branches.
- Added `.env` loading support via `python-dotenv` for systemd + local runs.

### Firestore Credential Resolution

- Fixed credential path fallback in `agents/todo-agent/db/firestore.py`.
- New behavior:
  1. Check `FIREBASE_SERVICE_ACCOUNT_KEY` and `GOOGLE_APPLICATION_CREDENTIALS` if file exists.
  2. Fallback to `/app/.secrets/serviceAccountKey.json`.
  3. Fallback to repo key if present.
  4. Otherwise initialize with default credentials.

### Deployment Hardening

- Rewrote `deploy.sh` with:
  - strict bash mode (`set -Eeuo pipefail`)
  - root check
  - required directory checks
  - idempotent venv + package installation
  - systemd daemon reload
  - nginx config installation + syntax test
  - explicit startup checks flow
- Updated systemd units (`systemd/*.service`):
  - `network-online.target`
  - optional `EnvironmentFile` support
  - unbuffered Python output for logs
- Updated nginx proxy config with keepalive and proxy timeout settings.

### Environment Documentation Cleanup

- Replaced `agents/teams-agent/.env.example` to remove obsolete Ollama variables.
- Kept only active Graph/Twilio/server settings.

## 3) Remaining Work (Not Yet Implemented)

1. API authentication between backend and EC2 agents (shared secret or JWT).
2. Rate limiting at Nginx layer (per IP / per user).
3. Token persistence for Microsoft Graph device flow (`auth_store` currently in memory).
4. Centralized observability (CloudWatch metrics/alarms + dashboard).
5. Blue/green or rolling deployment strategy for zero-downtime upgrades.
6. Automated health checks and rollback script.

## 4) EC2 Host Prerequisites

- Ubuntu EC2 instance with inbound rules:
  - `22` (SSH)
  - `80` (HTTP)
  - `443` (HTTPS)
- Domain is optional for now. Current host IP is `13.206.83.175` over HTTP.
- Code deployed at `/home/ubuntu/app`.

Repository paths expected by systemd/deploy script:

- `/home/ubuntu/app/agents/teams-agent`
- `/home/ubuntu/app/agents/todo-agent`
- `/home/ubuntu/app/agents/google-agent`
- `/home/ubuntu/app/systemd`
- `/home/ubuntu/app/nginx/sites-available/agents`

## 5) Deployment Steps

### Step 1: Sync Code to EC2

```bash
ssh -i <key>.pem ubuntu@<ec2-host>
cd /home/ubuntu/app
# pull/sync latest EC2 repo contents here
```

### Step 2: Configure Environment Files

`/home/ubuntu/app/agents/teams-agent/.env`

```bash
GRAPH_TENANT_ID=41503967-0840-4715-9d4d-1741979db5d9
GRAPH_CLIENT_ID=1f83edce-5c97-4110-b954-9234e87e5a03
PORT=8100
```

Optional Twilio keys can be added to same file.

`/home/ubuntu/app/agents/todo-agent/.env`

```bash
FIREBASE_SERVICE_ACCOUNT_KEY=/app/.secrets/serviceAccountKey.json
PORT=8200
```

`/home/ubuntu/app/agents/google-agent/.env`

```bash
GOOGLE_CLIENT_ID=<google-client-id>
GOOGLE_CLIENT_SECRET=<google-client-secret>
GOOGLE_REDIRECT_URI=http://localhost:3000/api/google-auth/callback
PORT=8300
```

### Step 3: Provision Firebase Service Account Key

```bash
sudo mkdir -p /app/.secrets
sudo cp /home/ubuntu/app/ai-everyone/serviceAccountKey.json /app/.secrets/serviceAccountKey.json
sudo chown root:root /app/.secrets/serviceAccountKey.json
sudo chmod 600 /app/.secrets/serviceAccountKey.json
```

### Step 4: Run Deployment Script

```bash
cd /home/ubuntu/app
chmod +x deploy.sh
sudo ./deploy.sh
```

### Step 5: Configure Nginx For Public IP

The repository already includes IP-based routing for `13.206.83.175`.
Re-copy config if needed, then validate and reload:

```bash
sudo cp /home/ubuntu/app/nginx/sites-available/agents /etc/nginx/sites-available/agents
sudo ln -sfn /etc/nginx/sites-available/agents /etc/nginx/sites-enabled/agents
sudo nginx -t
sudo systemctl restart nginx
```

### Step 6: TLS (Optional, Later)

Skip this until you attach a domain. Keep using HTTP on IP for now.

```bash
sudo certbot --nginx -d teams.<your-domain> -d todo.<your-domain>
```

### Step 7: Verify Runtime

```bash
curl http://127.0.0.1:8100/health
curl http://127.0.0.1:8200/health
curl http://127.0.0.1:8300/health

curl http://13.206.83.175/health
curl http://13.206.83.175/todo/health
curl http://13.206.83.175/google/health
curl -X POST http://13.206.83.175/teams/action -H "Content-Type: application/json" -d '{"action":"make_call","contact":"test@example.com"}'
curl -X POST http://13.206.83.175/todo/action -H "Content-Type: application/json" -d '{"taskId":"smoke-1","userId":"smoke-user","agentId":"todo-agent","action":"list_tasks"}'
curl -X POST http://13.206.83.175/google/action -H "Content-Type: application/json" -d '{"taskId":"smoke-2","userId":"smoke-user","agentId":"google-agent","agent_type":"gmail","action":"read_emails","parameters":"last 5 emails"}'

sudo systemctl status teams-agent --no-pager
sudo systemctl status todo-agent --no-pager
sudo systemctl status google-agent --no-pager
```

## 6) Firebase Cloud Functions Integration

Use backend-only calls. Browser should not call EC2 URLs directly.

### Required Function Environment

Set these in Cloud Functions runtime config or secret manager:

- `TEAMS_AGENT_BASE_URL=http://13.206.83.175`
- `TODO_AGENT_BASE_URL=http://13.206.83.175`
- `AGENT_SHARED_SECRET=<strong-random-value>` (recommended)

### Routing Pattern

Implement one dispatcher in Cloud Functions (example name: `executeAgentTask`) that:

1. Reads task payload (`taskId`, `userId`, `agentId`, `action`, params).
2. Selects endpoint by `agentId` and action family.
3. Sends signed HTTP request to EC2.
4. Persists response/result back to Firestore task document.

Reference implementation sketch:

```ts
import fetch from "node-fetch";

const AGENT_ROUTES: Record<string, string> = {
  "teams-agent": `${process.env.TEAMS_AGENT_BASE_URL}/teams/action`,
  "email-agent": `${process.env.TEAMS_AGENT_BASE_URL}/email/action`,
  "calendar-agent": `${process.env.TEAMS_AGENT_BASE_URL}/calendar/action`,
  "todo-agent": `${process.env.TODO_AGENT_BASE_URL}/todo/action`,
};

export async function forwardToAgent(task: any) {
  const url = AGENT_ROUTES[task.agentId];
  if (!url) throw new Error(`Unsupported agentId: ${task.agentId}`);

  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Agent-Secret": process.env.AGENT_SHARED_SECRET || "",
    },
    body: JSON.stringify(task),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Agent HTTP ${response.status}: ${text}`);
  }

  return response.json();
}
```

### Reliability Guidance

- Set function timeout >= 60s for Graph/API latency cases.
- Add retry with capped exponential backoff for `429/502/503/504`.
- Log `taskId`, `userId`, `agentId`, latency, and endpoint for traceability.

## 7) Browser-Side Setup (Important)

Browser must call your backend API/Cloud Function only.

Do:

- Browser -> Next.js API route / callable function -> EC2 agent

Do not:

- Browser -> `http://13.206.83.175/...` directly

Reasons:

- Protect secrets and routing logic.
- Enforce authentication/authorization centrally.
- Allow retries, redaction, and audit logging in backend layer.

Example browser call to backend endpoint:

```ts
await fetch("/api/agent/execute", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    agentId: "todo-agent",
    action: "add_task",
    title: "Prepare sprint review",
  }),
});
```

## 8) Adding A New Agent On EC2 (Expansion SOP)

When introducing `new-agent`:

1. Add service code:
   - Create `agents/new-agent/` with FastAPI app and `requirements.txt`.
2. Add systemd unit:
   - Create `systemd/new-agent.service` with unique port (example `8300`).
3. Add Nginx route:
   - Either new subdomain (`new-agent.<domain>`) or path routing.
4. Update deployment script:
   - Extend `deploy.sh` to create venv/install deps and enable service.
5. Update Cloud Functions router:
   - Map `agentId -> endpoint`.
6. Add health checks and smoke tests:
   - `/health`, one happy-path action, one failure-path action.
7. Update docs:
   - Add endpoint contracts to `API_DOCUMENTATION.md`.

Minimal verification checklist before release:

- `systemctl status new-agent` is active.
- `curl http://13.206.83.175/<new-agent-path>/health` returns healthy.
- Cloud Function route executes successfully.
- Error responses are propagated and logged with task metadata.

## 9) Operations and Troubleshooting

Common commands:

```bash
sudo systemctl restart teams-agent
sudo systemctl restart todo-agent
sudo systemctl reload nginx

journalctl -u teams-agent -f
journalctl -u todo-agent -f
```

If Nginx returns `502`:

1. Check upstream service status (`systemctl status`).
2. Validate local health (`curl http://127.0.0.1:<port>/health`).
3. Validate Nginx syntax (`nginx -t`) and reload.

If Todo agent fails Firebase init:

1. Verify `/app/.secrets/serviceAccountKey.json` exists.
2. Verify permissions `600`.
3. Check logs for selected credential path.

## 10) Security Notes

- Store all API secrets in Secret Manager or secure env injection.
- Restrict Security Group ingress to required ports only.
- Prefer backend-authenticated calls with a shared header/token.
- Rotate service credentials periodically.
