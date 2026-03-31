# EC2 Agent Deployment

This repository contains the EC2 runtime for SnitchX agent services.

## Repository Scope

- FastAPI agent services under `agents/`
- EC2 deployment automation in `deploy.sh`
- Linux service definitions in `systemd/`
- Reverse proxy configuration in `nginx/`
- Operational docs for deployment, APIs, and Firebase integration

## Current Services

### Existing Agents (ports 8100–8500)

| Service | Port | Endpoints |
|---|---|---|
| `teams-agent` | 8100 | `/teams/action`, `/email/action`, `/calendar/action`, `/auth/*` |
| `todo-agent` | 8200 | `/todo/action` |
| `google-agent` | 8300 | `/google/action`, `/google/auth/*` |
| `notion-agent` | 8400 | `/notion/action` |
| `maps-agent` | 8500 | `/maps/action` |

### New Integration Agents (ports 8001–8011)

| Service | Port | Action Endpoint | Health |
|---|---|---|---|
| `canva-agent` | 8001 | `/canva/action` | `/canva/health` |
| `day-planner-agent` | 8002 | `/dayplanner/action` | `/dayplanner/health` |
| `discord-agent` | 8003 | `/discord/action` | `/discord/health` |
| `dropbox-agent` | 8004 | `/dropbox/action` | `/dropbox/health` |
| `freshdesk-agent` | 8005 | `/freshdesk/action` | `/freshdesk/health` |
| `github-agent` | 8006 | `/github/action` | `/github/health` |
| `gitlab-agent` | 8007 | `/gitlab/action` | `/gitlab/health` |
| `greenhouse-agent` | 8008 | `/greenhouse/action` | `/greenhouse/health` |
| `jira-agent` | 8009 | `/jira/action` | `/jira/health` |
| `linkedin-agent` | 8010 | `/linkedin/action` | `/linkedin/health` |
| `zoom-agent` | 8011 | `/zoom/action` | `/zoom/health` |

All services are reverse-proxied through Nginx on port 80.

## Public Health Endpoints

```
http://13.206.83.175/health          ← teams
http://13.206.83.175/todo/health
http://13.206.83.175/google/health
http://13.206.83.175/notion/health
http://13.206.83.175/maps/health
http://13.206.83.175/canva/health
http://13.206.83.175/dayplanner/health
http://13.206.83.175/discord/health
http://13.206.83.175/dropbox/health
http://13.206.83.175/freshdesk/health
http://13.206.83.175/github/health
http://13.206.83.175/gitlab/health
http://13.206.83.175/greenhouse/health
http://13.206.83.175/jira/health
http://13.206.83.175/linkedin/health
http://13.206.83.175/zoom/health
```

## Deployment

```bash
# SSH into EC2
ssh -i "~/.ssh/agent-key.pem" ubuntu@13.206.83.175

# Pull latest code
cd /home/ubuntu/app && git pull

# Run deployment script (installs venvs, systemd units, reloads nginx)
sudo ./deploy.sh
```

## Documentation

- `DEPLOYMENT_RUNBOOK.md` — architecture, exact setup steps, completed changes, and SOP for adding new agents
- `API_DOCUMENTATION.md` — endpoint contracts, request/response payloads, integration patterns

## Important Repo Separation Rule

This EC2 repository is intentionally isolated from the SaaS web app repository.

- Do not copy web app files (`src/`, `public/`, `functions/`, `package.json`) into this repository.
- Do not deploy this repository through web app CI jobs.
- Keep all EC2 infrastructure and runtime changes inside this repository only.
