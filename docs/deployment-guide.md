# Deployment Guide

How to deploy the Wildlife Watcher V2 platform (backend + frontend) to production.

## Table of Contents

- [Environments](#environments)
- [Backend Deployment (Azure Container Apps)](#backend-deployment-azure-container-apps)
- [Frontend Deployment (Cloudflare Pages)](#frontend-deployment-cloudflare-pages)
- [Environment Variables](#environment-variables)
- [Supabase Setup](#supabase-setup)
- [CI/CD Pipeline](#cicd-pipeline)
- [Monitoring](#monitoring)
- [Scaling](#scaling)
- [Troubleshooting](#troubleshooting)

---

## Environments

| Component | Dev | Staging (current "prod") |
|-----------|-----|--------------------------|
| **Supabase** | `qegeovogqxiouqbrxmnh` (Dev_Wildlife_Watcher) | `nuhwmubvygxyddkycmpa` (Stag_Wildlife_Watcher) |
| **Azure Container App** | `ww-backend-dev` (WW-Website RG) | `ww-backend` (WW-Website RG) |
| **Azure Blob Container** | `wildlife-watcher-uploads-dev` | `wildlife-watcher-uploads` |
| **Frontend** | Cloudflare Pages preview deploys (per branch) | Cloudflare Pages (`ww-website.pages.dev` + `wildlifewatcher.ai`) |
| **Google Drive** | Dev subfolder under root folder | Root folder `1jIWV3OjSEnBK4Z64syHd2ugoRuXdVrK5` |

> **Seed data**: The dev Supabase project includes 17 test users (password: `test123`), 4 organisations, 5 projects, 9 devices, and 11 deployments. See `ww-backend/supabase/seeds/USER-CREDENTIALS-REFERENCE.md` for login details and `ww-backend/supabase/CLOUD_SEEDING.md` for the seeding workflow.

---

## Backend Deployment (Azure Container Apps)

The backend runs as a single containerised FastAPI application on **Azure Container Apps** (Consumption plan), deployed via **Azure Container Registry (ACR)**.

### Architecture

```
GitHub Actions (CI/CD)
  │
  ├── Build Docker image (backend/Dockerfile)
  ├── Push to Azure Container Registry (ACR)
  └── Update Azure Container App
        │
        ▼
Azure Container App ("ww-backend" or "ww-backend-dev")
  ├── FastAPI API Server (port 8000)
  ├── In-process async job runner (asyncio tasks)
  └── Supabase sync for job persistence (api_jobs table)
```

> **Note**: The target architecture adds Redis + ARQ Worker as separate containers. Currently, jobs run in-process with in-memory state synced to Supabase. See [docs/v2-architecture-plan.md](./v2-architecture-plan.md) for the full Redis+ARQ target.

### Manual Deployment

```bash
# 1. Build the Docker image
docker build -t <ACR_LOGIN_SERVER>/ww-backend:latest -f backend/Dockerfile backend/

# 2. Push to ACR
docker push <ACR_LOGIN_SERVER>/ww-backend:latest

# 3. Update the Container App
az containerapp update \
  --name ww-backend \
  --resource-group WW-Website \
  --image <ACR_LOGIN_SERVER>/ww-backend:latest
```

### Verify Deployment

```bash
# Get the FQDN
FQDN=$(az containerapp show \
  --name ww-backend \
  --resource-group WW-Website \
  --query "properties.configuration.ingress.fqdn" -o tsv)

# Health check
curl "https://${FQDN}/health"
# → {"status": "ok"}
```

---

## Frontend Deployment (Cloudflare Pages)

The frontend is a static React+Vite app deployed to **Cloudflare Pages** with automatic preview deployments per branch.

### Setup (One-Time)

1. Go to [Cloudflare Dashboard](https://dash.cloudflare.com/) → **Pages** → **Create a project** → **Connect to Git**
2. Select the `wildlifeai/ww-website` repository
3. Configure build settings:

   | Setting | Value |
   |---------|-------|
   | **Build command** | `cd frontend && npm install && npm run build` |
   | **Build output directory** | `frontend/dist` |
   | **Root directory** | `/` (repository root) |
   | **Node.js version** | `18` |

4. Set environment variables (in Cloudflare Pages settings):

   | Variable | Value |
   |----------|-------|
   | `SUPABASE_URL` | `https://nuhwmubvygxyddkycmpa.supabase.co` |
   | `SUPABASE_ANON_KEY` | _(from Supabase Dashboard)_ |
   | `VITE_API_BASE_URL` | `https://ww-backend.salmonsand-b067677e.australiasoutheast.azurecontainerapps.io` |

5. Assign custom domain: `wildlifewatcher.ai` (DNS is already on Cloudflare)

### Preview Deployments

Every push to a non-production branch creates a preview deployment at `https://<branch>.<project>.pages.dev`. This is automatic — no configuration needed.

---

## Environment Variables

See the [backend README](../backend/README.md#configuration) for the complete variable reference.

**Critical production checklist:**

- [ ] `SUPABASE_SERVICE_ROLE_KEY` is set and kept secret
- [ ] `ALLOWED_ORIGINS` is set to your exact frontend domain(s)
- [ ] `LORAWAN_WEBHOOK_SECRET` is set (not empty)
- [ ] `SENTRY_DSN` is set for error tracking
- [ ] `LOG_LEVEL` is `info` (not `debug`)
- [ ] `RATE_LIMIT_PER_MINUTE` is appropriate for your traffic
- [ ] `AZURE_STORAGE_CONNECTION_STRING` is set for image buffering
- [ ] `GOOGLE_DRIVE_ENABLED` is set to `true` if Drive uploads are needed

### Azure Container App Environment Variables

Set environment variables on the Container App via Azure CLI:

```bash
az containerapp update \
  --name ww-backend \
  --resource-group WW-Website \
  --set-env-vars \
    SUPABASE_URL=<value> \
    SUPABASE_ANON_KEY=<value> \
    SUPABASE_SERVICE_ROLE_KEY=secretref:supabase-service-key \
    ALLOWED_ORIGINS=https://wildlifewatcher.ai \
    LOG_LEVEL=info
```

> **Tip:** Use `secretref:` prefix for sensitive values. Create secrets first with `az containerapp secret set`.

---

## Supabase Setup

The backend expects these Supabase resources:

### Storage Buckets

| Bucket | Purpose | Public |
|--------|---------|--------|
| `firmware` | Config firmware, manifest results | Yes (mobile app downloads) |
| `ai-models` | AI model ZIPs | No (signed URLs) |

Create them in Supabase Dashboard → Storage → New Bucket.

### Database Tables

The backend reads/writes these tables (schema managed by `ww-backend` repo):

| Table | Used By | Access |
|-------|---------|--------|
| `devices` | LoRaWAN domain (device lookup by EUI) | RLS + service-role |
| `deployments` | LoRaWAN domain (active deployment match) | RLS + service-role |
| `ai_models` | Model domain (register/update) | RLS + service-role |
| `ai_model_families` | Model domain (family→firmware ID mapping) | RLS + service-role |
| `firmware` | Manifest domain (config firmware lookup) | RLS + service-role |
| `user_roles` | Dependencies (permission checks) | RLS + service-role |
| `lorawan_messages` | LoRaWAN domain (raw message store) | service-role only |
| `lorawan_parsed_messages` | LoRaWAN domain (parsed data store) | service-role only |
| `api_jobs` | Job system (status persistence + recovery) | service-role only |

### RPC Functions

| Function | Purpose |
|----------|---------|
| `check_user_uploader_role(p_user_id, p_org_id)` | Verifies user has upload permission |

### Realtime

Enable Realtime on `lorawan_parsed_messages` so the mobile app receives live updates:

1. Supabase Dashboard → Database → Replication
2. Enable `lorawan_parsed_messages` table for Realtime

---

## CI/CD Pipeline

### Backend: GitHub Actions → ACR → Azure Container App

The workflow `.github/workflows/deploy-backend.yml` triggers on pushes to `dev` and `main`:

| Branch | Target Container App | Image Tag |
|--------|---------------------|-----------|
| `dev` | `ww-backend-dev` | `dev-latest` + `<sha>` |
| `main` | `ww-backend` | `latest` + `<sha>` |

### Required GitHub Secrets

| Secret | Description |
|--------|-------------|
| `ACR_LOGIN_SERVER` | Azure Container Registry login server |
| `ACR_USERNAME` | ACR admin username |
| `ACR_PASSWORD` | ACR admin password |
| `AZURE_CREDENTIALS` | Azure service principal JSON (for `az login`) |
| `SUPABASE_URL` | Supabase project URL (for model deployment) |
| `SUPABASE_ANON_KEY` | Supabase anon key (for model deployment) |
| `GENERAL_ORG_ID` | `b0000000-0000-0000-0000-000000000001` |



---

## Monitoring

### Health Checks

The API exposes `/health` for automated monitoring:

```bash
# Simple check
curl -f https://<FQDN>/health || echo "API is down"

# Uptime monitoring services (UptimeRobot, Better Uptime, etc.)
# URL: https://<FQDN>/health
# Interval: 60 seconds
# Expected: 200 OK
```

### Structured Logging

All logs are JSON-formatted for easy ingestion into log aggregators:

```json
{
  "event": "request_complete",
  "method": "POST",
  "path": "/api/manifest/generate",
  "status_code": 200,
  "duration_ms": 42.3,
  "request_id": "a1b2c3d4-..."
}
```

### Azure Container App Logs

```bash
# View recent logs
az containerapp logs show \
  --name ww-backend \
  --resource-group WW-Website \
  --type console \
  --follow

# View system logs (crashes, restarts)
az containerapp logs show \
  --name ww-backend \
  --resource-group WW-Website \
  --type system
```

### Sentry

For error tracking, set `SENTRY_DSN` to your Sentry project DSN:

```
SENTRY_DSN=https://abc123@o456.ingest.sentry.io/789
```

This automatically captures:
- Unhandled exceptions
- Performance traces (10% sample rate)
- Request context (URL, method, headers)

---

## Scaling

### Azure Container Apps

| Setting | Dev | Staging/Prod |
|---------|-----|--------------|
| `min-replicas` | 0 (scale to zero) | 1 |
| `max-replicas` | 1 | 3 |
| CPU | 0.25 vCPU | 0.5 vCPU |
| Memory | 0.5 Gi | 1 Gi |

```bash
# Scale staging
az containerapp update \
  --name ww-backend \
  --resource-group WW-Website \
  --min-replicas 1 \
  --max-replicas 3 \
  --cpu 0.5 \
  --memory 1.0Gi
```

### Future: Redis + ARQ Worker

When Redis+ARQ is implemented, the worker will run as a separate Container App:

```bash
az containerapp create \
  --name ww-worker \
  --resource-group WW-Website \
  --image <ACR>/ww-backend:latest \
  --command "arq" "app.jobs.worker.WorkerSettings" \
  --min-replicas 1 \
  --max-replicas 3
```

---

## Troubleshooting

### Common Issues

**App won't start: `ValidationError` on startup**

Missing required environment variables. Check that `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY` are set.

```bash
# Verify env vars are loaded
az containerapp show \
  --name ww-backend \
  --resource-group WW-Website \
  --query "properties.template.containers[0].env"
```

**Jobs stuck in `queued` status**

Jobs run in-process as asyncio background tasks. If the container restarts mid-job, the job store's `recover_stuck_jobs()` function marks interrupted jobs as `failed` on next boot. Check container restart logs:

```bash
az containerapp logs show \
  --name ww-backend \
  --resource-group WW-Website \
  --type system
```

**Model conversion fails: "Vela command not found"**

The `ethos-u-vela` package needs to be installed in the container. Verify it's in `requirements.txt` and the Docker image was rebuilt.

**Rate limiting is too aggressive**

Increase the limit via environment variable:

```
RATE_LIMIT_PER_MINUTE=120
```

**CORS errors from frontend**

Add your frontend origin to `ALLOWED_ORIGINS`:

```
ALLOWED_ORIGINS=https://wildlifewatcher.ai,http://localhost:5173
```

**LoRaWAN webhooks returning 401**

Webhook secret mismatch. Verify the secret matches between your network server and `LORAWAN_TTN_WEBHOOK_SECRET` / `LORAWAN_CHIRPSTACK_WEBHOOK_SECRET`.
