# EC2 Production Readiness Audit

Date: 2026-04-19  
Scope: `EC2/` runtime only (all EC2-hosted agents and shared infra)

## Shared Issues Found

1. Hardcoded and insecure public base assumptions (`http://35.154.54.246`) across runtime defaults and examples.
2. CORS posture inconsistent and overly permissive (`allow_origins=["*"]` on most agents).
3. OAuth callback flow lacked return-origin allowlist enforcement.
4. systemd units pinned to `--workers 1`, limiting concurrent throughput under load.
5. systemd hardening knobs missing (`NoNewPrivileges`, FD limits, timeout).
6. Nginx edge lacked baseline security headers and per-route rate limiting.
7. Deployment script allowed unsafe/implicit env fallbacks and did not validate critical OAuth env.
8. Env examples were machine/IP-specific instead of domain/TLS-first.

## Fixes Implemented

1. Added shared API hardening module: [api_security.py](/e:/SaaS-ai/ai-everyone/EC2/ec2_shared/api_security.py)
   - centralized CORS allowlist (`CORS_ORIGINS`)
   - trusted host validation (`TRUSTED_HOSTS`)
   - default security headers on all responses
2. Applied shared security middleware to all EC2 FastAPI app entrypoints that previously used ad-hoc CORS.
3. Hardened OAuth router: [oauth_router.py](/e:/SaaS-ai/ai-everyone/EC2/ec2_shared/oauth_router.py)
   - return-origin normalization + allowlist (`OAUTH_ALLOWED_RETURN_ORIGINS`)
   - safer Google redirect behavior when insecure redirect overrides are configured
   - no wildcard `postMessage` target for OAuth popup completion
4. Removed hardcoded public base fallback from [agent_runtime.py](/e:/SaaS-ai/ai-everyone/EC2/ec2_shared/agent_runtime.py).
5. Hardened Nginx edge: [agents](/e:/SaaS-ai/ai-everyone/EC2/nginx/sites-available/agents)
   - `server_name _` (no pinned IP host binding)
   - security headers
   - request rate limits for action routes and OAuth auth routes
6. Upgraded systemd units (tracked services):
   - `--workers 2`
   - `LimitNOFILE=65535`
   - `TimeoutStopSec=30`
   - `NoNewPrivileges=true`
   - `PrivateTmp=true`
7. Strengthened deploy validation: [deploy.sh](/e:/SaaS-ai/ai-everyone/EC2/deploy.sh)
   - strict env validation mode (`STRICT_ENV_VALIDATION`, default `1`)
   - checks for `AGENT_PUBLIC_BASE_URL` and `AGENT_OAUTH_SHARED_SECRET`
   - validates OAuth agent `.env` presence + required keys
8. Standardized env templates:
   - Added shared template: [EC2/.env.template](/e:/SaaS-ai/ai-everyone/EC2/.env.template)
   - Updated OAuth agent `.env.example` files to domain/TLS-first values.

## Per-Agent Findings and Status

| Agent | Issues Identified | Resolution |
|---|---|---|
| teams-agent | permissive app CORS, single-worker service | migrated to shared security middleware + service hardening |
| todo-agent | permissive app CORS in API layer, single-worker service | migrated to shared security middleware + service hardening |
| google-agent | historical redirect mismatch risk, permissive app CORS, single-worker service | OAuth flow hardened + shared security middleware + service hardening |
| notion-agent | permissive app CORS, single-worker service | migrated to shared security middleware + service hardening |
| maps-agent | permissive app CORS, single-worker service | migrated to shared security middleware + service hardening |
| emergency-response-agent | permissive app CORS, single-worker service | migrated to shared security middleware + service hardening |
| strata-agent | permissive app CORS, single-worker service | migrated to shared security middleware + service hardening |
| canva-agent | permissive app CORS, single-worker service | migrated to shared security middleware + service hardening |
| day-planner-agent | permissive app CORS, single-worker service | migrated to shared security middleware + service hardening |
| discord-agent | permissive app CORS, single-worker service | migrated to shared security middleware + service hardening |
| dropbox-agent | permissive app CORS, single-worker service | migrated to shared security middleware + service hardening |
| freshdesk-agent | permissive app CORS, single-worker service | migrated to shared security middleware + service hardening |
| github-agent | permissive app CORS, single-worker service | migrated to shared security middleware + service hardening |
| gitlab-agent | permissive app CORS, single-worker service | migrated to shared security middleware + service hardening |
| greenhouse-agent | permissive app CORS, single-worker service | migrated to shared security middleware + service hardening |
| jira-agent | permissive app CORS, single-worker service | migrated to shared security middleware + service hardening |
| linkedin-agent | permissive app CORS, single-worker service | migrated to shared security middleware + service hardening |
| zoom-agent | permissive app CORS, single-worker service | migrated to shared security middleware + service hardening |
| dia-helper-agent | permissive app CORS, single-worker service | migrated to shared security middleware + service hardening |
| shopgenie-agent | permissive app CORS, single-worker service | migrated to shared security middleware + service hardening |
| career-switch-agent | permissive app CORS, single-worker service | migrated to shared security middleware + service hardening |
| dashboard-designer-agent | permissive app CORS, single-worker service | migrated to shared security middleware + service hardening |
| smart-gtm-agent | permissive app CORS, single-worker service | migrated to shared security middleware + service hardening |
| seo-agent | permissive app CORS, single-worker service | migrated to shared security middleware + service hardening |
| startup-fundraising-agent | permissive app CORS, single-worker service | migrated to shared security middleware + service hardening |
| ats-agent | permissive app CORS in new service path | migrated to shared security middleware (service file present in repo but currently untracked) |
| building-construction-agent | permissive app CORS in new service path | migrated to shared security middleware (service file present in repo but currently untracked) |
| lms-agent | permissive app CORS in new service path | migrated to shared security middleware (service file present in repo but currently untracked) |

## Remaining External Tasks (Required for Full Production)

1. Put EC2 traffic behind HTTPS (ALB or Nginx TLS cert) and use an HTTPS `AGENT_PUBLIC_BASE_URL`.
2. Set and rotate `AGENT_OAUTH_SHARED_SECRET` across web backend and EC2.
3. Populate strict `CORS_ORIGINS`, `TRUSTED_HOSTS`, and `OAUTH_ALLOWED_RETURN_ORIGINS` for prod/staging.
4. Ensure every provider console OAuth redirect exactly matches configured callback URLs.
5. Add centralized monitoring/alerts (CloudWatch alarms, structured log aggregation, request/error SLO dashboards).
6. Capacity test with concurrent load and tune worker/process counts per agent profile.
