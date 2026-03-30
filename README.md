# EC2 Agent Deployment

This repository contains the EC2 runtime for SnitchX agent services.

## Repository Scope

- FastAPI agent services under `agents/`
- EC2 deployment automation in `deploy.sh`
- Linux service definitions in `systemd/`
- Reverse proxy configuration in `nginx/`
- Operational docs for deployment, APIs, and Firebase integration

## Current Services

- `teams-agent` on port `8100`
  - Endpoints: `/teams/action`, `/email/action`, `/calendar/action`, auth endpoints
- `todo-agent` on port `8200`
  - Endpoint: `/todo/action`
- `google-agent` on port `8300`
  - Endpoints: `/google/action`, auth endpoints

Current public host mode (without domain):

- `http://13.206.83.175/health` (teams health)
- `http://13.206.83.175/todo/health` (todo health)
- `http://13.206.83.175/google/health` (google health)
- `http://13.206.83.175/teams/action`
- `http://13.206.83.175/todo/action`
- `http://13.206.83.175/google/action`

## Documentation

- `DEPLOYMENT_RUNBOOK.md` - deployment architecture, exact setup steps, completed changes, pending work, scaling process for new agents
- `API_DOCUMENTATION.md` - endpoint contracts, request/response payloads, integration patterns, and error handling expectations

## Important Repo Separation Rule

This EC2 repository is intentionally isolated from the SaaS web app repository.

- Do not copy web app files (`src/`, `public/`, `functions/`, `package.json`) into this repository.
- Do not deploy this repository through web app CI jobs.
- Keep all EC2 infrastructure and runtime changes inside this repository only.
