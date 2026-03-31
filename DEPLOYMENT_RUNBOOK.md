# EC2 Deployment Runbook

This runbook is the source of truth for deploying the detached EC2 agent repo.

## Architecture

- Only `EC2/` is deployed to the EC2 instance.
- The main AI Everyone web application is not present on the EC2 host.
- Each folder under `EC2/agents` is a self-contained microservice with its own `main.py`, `server.py`, env handling, and action logic.
- Nginx exposes public routes and forwards traffic to local FastAPI services.
- OAuth is owned by the EC2 agents themselves, not by the main web app runtime.

## Repository Layout On Host

Expected deploy location:

- `/home/ubuntu/app`

Expected subpaths:

- `/home/ubuntu/app/agents/<agent-name>`
- `/home/ubuntu/app/systemd`
- `/home/ubuntu/app/nginx/sites-available/agents`
- `/home/ubuntu/app/.secrets/serviceAccountKey.json`

## Required Secrets And Env

Shared across OAuth agents:

- `AGENT_OAUTH_SHARED_SECRET=<shared signing secret used by web app + EC2>`
- `AGENT_PUBLIC_BASE_URL=http://13.206.83.175`
- `FIREBASE_SERVICE_ACCOUNT_KEY=/home/ubuntu/app/.secrets/serviceAccountKey.json`

Google:

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- OAuth redirect URI in Google console:
  `http://13.206.83.175/google/auth/callback`

Microsoft / Teams:

- `GRAPH_TENANT_ID`
- `GRAPH_CLIENT_ID` or `MICROSOFT_CLIENT_ID`
- `MICROSOFT_CLIENT_SECRET`
- OAuth redirect URI in Azure app:
  `http://13.206.83.175/teams/auth/callback`

Other OAuth agents:

- `NOTION_CLIENT_ID` / `NOTION_CLIENT_SECRET`
- `CANVA_CLIENT_ID` / `CANVA_CLIENT_SECRET`
- `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET`
- `DROPBOX_CLIENT_ID` / `DROPBOX_CLIENT_SECRET`
- `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET`
- `GITLAB_CLIENT_ID` / `GITLAB_CLIENT_SECRET`
- `JIRA_CLIENT_ID` / `JIRA_CLIENT_SECRET`
- `LINKEDIN_CLIENT_ID` / `LINKEDIN_CLIENT_SECRET`
- `ZOOM_CLIENT_ID` / `ZOOM_CLIENT_SECRET`

API-key agents:

- `FRESHDESK_API_KEY`
- `FRESHDESK_DOMAIN`
- `GREENHOUSE_API_KEY`

## Deployment Steps

1. SSH into EC2.

```bash
ssh -i <key>.pem ubuntu@13.206.83.175
```

2. Pull the detached EC2 repo.

```bash
cd /home/ubuntu/app
git pull
```

3. Place the Firebase service account.

```bash
sudo mkdir -p /home/ubuntu/app/.secrets
sudo cp /path/to/serviceAccountKey.json /home/ubuntu/app/.secrets/serviceAccountKey.json
sudo chown root:root /home/ubuntu/app/.secrets/serviceAccountKey.json
sudo chmod 600 /home/ubuntu/app/.secrets/serviceAccountKey.json
```

4. Fill in each agent `.env` file.

At minimum every OAuth agent needs:

```bash
PORT=<service-port>
AGENT_PUBLIC_BASE_URL=http://13.206.83.175
AGENT_OAUTH_SHARED_SECRET=<same-secret-as-web-app>
FIREBASE_SERVICE_ACCOUNT_KEY=/home/ubuntu/app/.secrets/serviceAccountKey.json
```

5. Run the deploy script.

```bash
cd /home/ubuntu/app
chmod +x deploy.sh
sudo ./deploy.sh
```

## What `deploy.sh` Does

- validates required directories
- creates or updates per-agent Python virtualenvs
- installs each agent’s requirements
- installs and reloads all systemd services
- installs and validates the Nginx config
- runs local health checks
- runs public health checks
- probes public OAuth routes

## Public Route Summary

Health:

- `/health`
- `/teams/health`
- `/todo/health`
- `/google/health`
- `/notion/health`
- `/maps/health`
- `/canva/health`
- `/dayplanner/health`
- `/discord/health`
- `/dropbox/health`
- `/freshdesk/health`
- `/github/health`
- `/gitlab/health`
- `/greenhouse/health`
- `/jira/health`
- `/linkedin/health`
- `/zoom/health`

OAuth:

- `/teams/auth/*`
- `/google/auth/*`
- `/notion/auth/*`
- `/canva/auth/*`
- `/discord/auth/*`
- `/dropbox/auth/*`
- `/github/auth/*`
- `/gitlab/auth/*`
- `/jira/auth/*`
- `/linkedin/auth/*`
- `/zoom/auth/*`

## Post-Deploy Verification

Local:

```bash
curl http://127.0.0.1:8100/health
curl http://127.0.0.1:8300/health
curl http://127.0.0.1:8400/health
```

Public:

```bash
curl http://13.206.83.175/teams/health
curl http://13.206.83.175/google/health
curl http://13.206.83.175/notion/health
curl -i http://13.206.83.175/google/auth/login
curl -i http://13.206.83.175/teams/auth/login
```

Service status:

```bash
sudo systemctl status teams-agent --no-pager
sudo systemctl status google-agent --no-pager
sudo systemctl status notion-agent --no-pager
```

## Operational Notes

- EC2 OAuth login requires a signed `handoff` token from the web app/backend.
- Action handlers can load saved provider credentials from Firestore by `userId`.
- `canva-agent` is intentionally a coming-soon placeholder because the JS source of truth still behaves that way.
- `freshdesk-agent` and `jira-agent` intentionally use JS-parity stub responses instead of live API behavior.
