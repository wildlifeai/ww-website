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

> **All Azure resources live in one resource group — `WW-AE`, region `australiaeast`.** The old
> `WW-Website` RG was deleted on 2026-06-30. The authoritative inventory of what exists is
> [cloud-infrastructure.md](cloud-infrastructure.md).

| Component | Dev | Staging (current "prod") |
|-----------|-----|--------------------------|
| **Supabase** | `qegeovogqxiouqbrxmnh` (Dev_Wildlife_Watcher) | `nuhwmubvygxyddkycmpa` (Stag_Wildlife_Watcher) |
| **Azure Container App** | `ww-backend-dev` (WW-AE RG) | `ww-backend` (WW-AE RG) |
| **Azure Blob Container** | `wildlife-watcher-uploads-dev` | `wildlife-watcher-uploads` (storage account `wwuploadsae`) |
| **Frontend** | Cloudflare Pages preview deploys (per branch) | Cloudflare Pages (`ww-website.pages.dev` + `wildlifewatcher.ai`) |
| **Google Drive** | `Data/dev` — `1-F6cQc5lpYOJNSPKRs7i79Gh539N5hUT` | `Data/Production` — `1apf13KX075Fv4K0A2nbaTqOmCwZ9vJzr` |

> **Seed data**: The dev Supabase project is seeded with test users, organisations, projects, devices
> and deployments. Counts and credentials are **not** duplicated here —
> `ww-backend/documentation/resources/USER-CREDENTIALS-REFERENCE.md` is canonical for logins and
> `ww-backend/documentation/resources/CLOUD_SEEDING.md` for the seeding workflow. The shared password comes from
> `SEED_USER_PASSWORD` (a GitHub secret, never hardcoded) — see
> [testing-with-seed-users](./testing-with-seed-users.md).

---

## Backend Deployment (Azure Container Apps)

The backend runs as a containerised FastAPI application on **Azure Container Apps** (Consumption plan), deployed via **Azure Container Registry (ACR)**.

### Architecture

```
GitHub Actions (CI/CD)
  │
  ├── Build Docker image (backend/Dockerfile, --target api)
  ├── Push to Azure Container Registry (wwregistry.azurecr.io)
  └── Update Azure Container App
        │
        ▼
Azure Container App ("ww-backend" / "ww-backend-dev")  ──REDIS_URL set──▶  ARQ GPU worker
  ├── FastAPI API Server (port 8000)                                       ("ww-embedding-worker-dev",
  ├── Job dispatch: ARQ when REDIS_URL is set, else in-process asyncio       --target worker, serverless T4)
  └── Supabase sync for job persistence (api_jobs table)
```

> **Note**: heavy ML work (SpeciesNet, BioCLIP, DINOv3) runs in a **separate ARQ GPU worker**, not in
> the API container — the lean `--target api` image carries no ML deps. The worker is **live on dev**
> (serverless T4, scale-to-zero, since 2026-07-03); **production is API-only** until the prod worker is
> provisioned. With `REDIS_URL` unset, `dispatch.py` falls back to running jobs in-process.
> Live state: [cloud-infrastructure.md](cloud-infrastructure.md) · stand up the prod worker:
> [prod-worker-provisioning-runbook.md](prod-worker-provisioning-runbook.md).

### Manual Deployment

```bash
# 1. Build the Docker image (--target api: the lean image, no ML deps)
docker build --target api -t wwregistry.azurecr.io/ww-backend:latest -f backend/Dockerfile backend/

# 2. Push to ACR
docker push wwregistry.azurecr.io/ww-backend:latest

# 3. Update the Container App
az containerapp update \
  --name ww-backend \
  --resource-group WW-AE \
  --image wwregistry.azurecr.io/ww-backend:latest
```

### Verify Deployment

```bash
# Get the FQDN
FQDN=$(az containerapp show \
  --name ww-backend \
  --resource-group WW-AE \
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
   | **Node.js version** | `20` (match [00-GETTING-STARTED](../onboarding/00-GETTING-STARTED.md#prerequisites)) |

4. Set environment variables (in Cloudflare Pages settings):

   | Variable | Value |
   |----------|-------|
   | `SUPABASE_URL` | `https://nuhwmubvygxyddkycmpa.supabase.co` |
   | `SUPABASE_ANON_KEY` | _(from Supabase Dashboard)_ |
   | `VITE_API_BASE_URL` | `https://ww-backend.bravesand-8bd2f1d4.australiaeast.azurecontainerapps.io` |

   > The dev-preview equivalent is `https://ww-backend-dev.bravesand-8bd2f1d4.australiaeast.azurecontainerapps.io`.
   > Re-check both against `cloud-infrastructure.md` before pasting — the FQDNs changed with the
   > 2026-06-30 move to `WW-AE` / Australia East.

5. Assign custom domain: `wildlifewatcher.ai` (DNS is already on Cloudflare)

### Preview Deployments

Every push to a non-production branch creates a preview deployment at `https://<branch>.<project>.pages.dev`. This is automatic — no configuration needed.

---

## Environment Variables

See [00-GETTING-STARTED.md](../onboarding/00-GETTING-STARTED.md#environment-variables) for the complete variable reference.

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
  --resource-group WW-AE \
  --set-env-vars \
    SUPABASE_URL=<value> \
    SUPABASE_ANON_KEY=<value> \
    SUPABASE_SERVICE_ROLE_KEY=secretref:supabase-service-key \
    ALLOWED_ORIGINS=https://wildlifewatcher.ai \
    LOG_LEVEL=info
```

> **Tip:** Use `secretref:` prefix for sensitive values. Create secrets first with `az containerapp secret set`.

### Full-pipeline config checklist (per subsystem)

A **fresh container only has the env you explicitly set** — feature flags and storage default to off/empty in code. `az containerapp update --set-env-vars` **merges** (it won't wipe existing vars), but a newly-created container needs the whole set below or the upload → store → AI → annotate flow silently no-ops. Each row maps a **symptom you'd see if it's missing** to the vars that fix it.

**Core (required to boot at all)**

| Var | Notes |
|-----|-------|
| `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` | DB + auth. Service-role as `secretref:`. |
| `ALLOWED_ORIGINS` | Exact frontend origin(s). |
| `GENERAL_ORG_ID` | Default org for auto-assignment. |
| `REDIS_URL` | **ARQ job queue.** Empty → in-memory fallback (no durable/cross-container jobs). The **worker needs the same value.** |

**Original-image storage** — *missing → uploads "complete" but produce no usable media; `GET /api/media/:id/image` returns **422** ("cannot be resolved"):*

| Var | Enables |
|-----|---------|
| `AZURE_STORAGE_CONNECTION_STRING` (+ `AZURE_STORAGE_CONTAINER_NAME`) | Blob buffering of originals — **this is the one that bites.** The container *name* alone does nothing; without the **connection string** every upload logs `azure_storage_not_configured_for_store` and no media rows are created. |
| `SUPABASE_MEDIA_BUCKET` | Supabase Storage bucket for media. |
| `GOOGLE_DRIVE_ENABLED=true` + `GOOGLE_SERVICE_ACCOUNT_JSON` + `GOOGLE_DRIVE_FOLDER_ID` | Drive archival. **Folder ID alone does nothing** — without `ENABLED` + the service account, queued uploads stay `pending_drive_uploads` and the images never become servable. |

**AI pipeline** — *missing → "No animals detected"; no `speciesnet`/`pipeline` log lines:*

| Var | Enables |
|-----|---------|
| `FF_PIPELINE_ENABLED=true` | Inference endpoints. |
| `FF_SPECIESNET_ENABLED=true` (+ `SPECIESNET_RUN_MODE`) | SpeciesNet detector+classifier. **Runs in the ARQ worker, not the API image (`--target api`)** — confirm the worker container is deployed with the same flags + `REDIS_URL` + GPU. |
| `FF_MEDIA_REGISTRY_ENABLED=true` | Thumbnails / animal crops (the **Labels** view is empty without crops). |
| `FF_BIOCLIP_ENABLED`, `FF_WILDLIFE_BRAIN_ENABLED` (+ `HF_TOKEN` for the gated DINOv3 weights, `EMBEDDING_*`; the vector store is **pgvector** in Supabase — no `QDRANT_*` vars, Qdrant is removed) | BioCLIP + DINOv3 embeddings / clustering. See [Vector Store](#vector-store--pgvector-supabase). |
| `FF_PER_CROP_CLASSIFY_ENABLED` | Per-detection (per-crop) species — one observation per animal, BioCLIP refines each crop. **Requires the GPU worker**; default off (collapses per image when off). |

**Demo account** — *missing → "Try the demo" self-disables (`DEMO_DISABLED`):*

| Var | Notes |
|-----|-------|
| `DEMO_EMAIL`, `DEMO_PASSWORD` | Must match the seeded demo user (`DEMO_PASSWORD` = dev `SEED_USER_PASSWORD`). See [demo-account.md](./demo-account.md). |

**Integrations (optional)**

| Var | Enables |
|-----|---------|
| `INAT_CLIENT_ID`, `INAT_CLIENT_SECRET`, `INAT_REDIRECT_URI` + `FF_INAT_ENABLED` | iNaturalist sync/publish. |
| `LORAWAN_*_WEBHOOK_SECRET` | LoRaWAN webhooks. |
| `EMAIL_PROVIDER`, `EMAIL_FROM`, `RESEND_API_KEY` / `SENDGRID_API_KEY` / `ACS_CONNECTION_STRING` | Notification email. |
| `SENTRY_DSN`, `LOG_LEVEL` | Error tracking / log verbosity. |

> **Quick audit** of a container's current env:
> ```bash
> az containerapp show --name ww-backend-dev -g WW-AE \
>   --query "properties.template.containers[0].env[].name" -o tsv | sort
> ```
> If `GOOGLE_DRIVE_ENABLED`, `FF_PIPELINE_ENABLED`, `FF_SPECIESNET_ENABLED`, or `FF_MEDIA_REGISTRY_ENABLED` are absent, that subsystem is off regardless of what the UI seems to show.

---

## Supabase Setup

The backend expects these Supabase resources:

### Storage Buckets

| Bucket | Purpose | Public |
|--------|---------|--------|
| `firmware` | Config firmware, manifest results | Yes (mobile app downloads) |
| `ai-models` | Compiled models — `{fw_id}V{ver}.TFL` + `.TXT` stored as **independent blobs, no ZIP** ([ai-model-pipeline](./ai-model-pipeline.md#storage-architecture)) | No (signed URLs) |
| `media-renditions` | Thumbnails / previews / animal crops (`SUPABASE_MEDIA_BUCKET`) — the Annotations grid reads these instead of hitting Google Drive | **Yes** |

Create them in Supabase Dashboard → Storage → New Bucket.

> ⚠️ A **missing `media-renditions` bucket** was one of the three parity gaps that silently broke
> production in July 2026 (green ticks over an empty grid) — see the
> [parity audit](cloud-infrastructure.md#dev--prod-parity-audit). Create it in every environment.

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

## Vector Store — pgvector (Supabase)

The **Wildlife Brain** (DINOv3 embeddings → clustering → similarity search) needs a vector store, and
the **vector store is `pgvector` in the Supabase Postgres** we already run. Vectors live in an unbounded
`vector` column (`media_embeddings.embedding`; dim per model — 384 vits / 1280 vith) beside the relational
data, so they inherit RLS + PITR backup and add **no new vendor or ops surface**. At this project's scale (one DINOv3 vector per crop → hundreds of thousands,
low millions) pgvector's HNSW is comfortably sufficient, and embedding/cluster queries are
`deployment_id`/`project_id`-scoped (small candidate sets). If scale ever demands more, **pgvectorscale**
(StreamingDiskANN) lifts the ceiling *without leaving Postgres* — there is no plan to adopt a separate
vector database.

> ✅ **Migration complete (2026-07-09) — pgvector is live; Qdrant is removed.** The Brain runs
> end-to-end on dev (embed → cluster → similarity). The Qdrant *service* is gone: `qdrant_client.py`,
> the compose container, `QDRANT_*` config and the `qdrant-client` dependency have all been deleted.
> **The name survives in one place** — `qdrant_collection` is still the field naming the logical vector
> space in `registries/embedding_registry.py`, `domain/wildlife_brain.py` and `embedding_runs`. It is a
> label, not a dependency; renaming it to `vector_space` needs a coordinated ww-backend column change.

**As built** (schema owned by [`ww-backend`](https://github.com/wildlifeai/wildlife-watcher-backend)):
1. **`ww-backend`:** the `vector` extension is enabled (in the `extensions` schema) and the vector lives
   on **`media_embeddings.embedding`** — declared **unbounded** `extensions.vector` (not `vector(1280)`)
   so one column holds both DINOv3 variants (384-d `dinov3-vits` / 1280-d `dinov3-vith`). Search is the
   **`match_media_embeddings`** RPC (cosine `<=>`). **No ANN index yet** — candidate sets are
   `deployment_id`/run-scoped and small, so an exact scan is fine; an **HNSW index is the scale lever**
   if that changes (see [Scaling](#scaling)).
2. **`ww-website`:** `PgVectorService` sits behind the store-agnostic `get_vector_service()` seam
   (`embedding_runs` is store-agnostic); the Qdrant service, `QDRANT_*` config, compose container, and
   `qdrant-client` dependency are gone.
3. Each row is stamped with **`embedding_model`** and the RPC **filters on it at read time**
   (`WHERE embedding_model = $1`, plus a `CASE` guard on `<=>`) so a model/weights change can't mix
   incompatible vector dims. DR is automatic — the vectors are a Postgres table under Supabase PITR, so
   the `/api/brain/backup` job is now a **no-op**.

> The heavy ML inference (DINOv3/SpeciesNet on GPU) is a separate concern — see the `gpu` profile +
> `embedding-worker` in `docker-compose.yml`, which also needs Redis + ARQ before it can run as a
> cloud worker (the API currently runs jobs in-process; see [Scaling](#scaling)).

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
  --resource-group WW-AE \
  --type console \
  --follow

# View system logs (crashes, restarts)
az containerapp logs show \
  --name ww-backend \
  --resource-group WW-AE \
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

> `min-replicas` for the API is **set by [`deploy-backend.yml`](../../.github/workflows/deploy-backend.yml)
> on every deploy** (dev 0, prod 1). A manual `az containerapp update --min-replicas …` is overwritten by
> the next deploy of that branch — change the workflow's *Determine target* step instead.

| Setting | Dev | Staging/Prod |
|---------|-----|--------------|
| `min-replicas` | 0 (scale to zero) | 1 (keeps the public demo button warm) |
| `max-replicas` | 1 | 3 |
| CPU | 0.25 vCPU | 0.5 vCPU |
| Memory | 0.5 Gi | 1 Gi |

```bash
# Scale staging
az containerapp update \
  --name ww-backend \
  --resource-group WW-AE \
  --min-replicas 1 \
  --max-replicas 3 \
  --cpu 0.5 \
  --memory 1.0Gi
```

### GPU Worker + Scale-to-Zero (the ML worker)

Heavy ML (SpeciesNet detect, BioCLIP classify, DINOv3 embeddings) runs in a **separate ARQ worker** on
a serverless T4 GPU that scales to zero when idle. The API keeps the lean `--target api` image. The seam:

- [`dispatch.py`](../../backend/app/jobs/dispatch.py): `REDIS_URL` set → `enqueue_job` pushes to Redis and the worker runs the job; empty → in-process. The lean API image never imports torch/SpeciesNet.
- Dockerfile **`worker`** target bundles `requirements-ml.txt` (torch, transformers, hdbscan, umap, speciesnet); its CMD is `arq app.jobs.worker.WorkerSettings`.
- [`worker.py`](../../backend/app/jobs/worker.py) auto-registers every function in `definitions.JOBS` — including `annotate_deployments_job`, the offload target the upload flow enqueues when `REDIS_URL` is set.
- Job status is mirrored to Supabase `api_jobs`, so the API's `/api/jobs/{id}` polling works regardless of which process ran the job (cross-process by design).

**The provisioning commands are deliberately not repeated here.** Three docs own this, and keeping a
fourth copy is what let this section drift out of date:

| What you need | Doc |
|---|---|
| **What exists today**, resource by resource, with the live gotchas | [cloud-infrastructure.md](cloud-infrastructure.md#azure) |
| **How to stand up the production worker**, step by step | [prod-worker-provisioning-runbook.md](prod-worker-provisioning-runbook.md) |
| **Why it is shaped this way** (design history, batching, retries, DLQ) | [gpu-worker-infra-spec.md](../development%20reports/gpu-worker-infra-spec.md) |

Three facts differ from the obvious guess, so check them before you touch anything:

- **Redis is a Container App, not Azure Cache for Redis.** `ww-redis-dev` runs `redis:7-alpine` with
  internal TCP ingress on 6379. Reach it app-to-app by **short name** — `redis://ww-redis-dev:6379`;
  the `.internal.*` FQDN times out for TCP.
- **The worker does not scale on Redis.** The ACA KEDA operator cannot reach that internal Redis, so
  scaling uses a **KEDA PostgreSQL scaler** querying `api_jobs` on the matching Supabase project, over
  the **shared Session pooler on :5432** (Transaction pooling rejects the scaler's prepared statements
  with `42P05`). The `ww:gpu:pending` list marker in `dispatch.py`/`worker.py` is inert here — it was
  written for a Redis-scaler design that ACA networking ruled out.
- **Never set the scale rule with `az containerapp update --scale-rule-metadata`.** The CLI silently
  drops metadata values containing spaces (i.e. the SQL), leaving `query` empty — with `min=0` the
  worker then never wakes and uploads queue forever. Use the Portal or a full `--yaml`.

| Component | Azure resource | Replicas | Rough cost |
|-----------|----------------|----------|-----------|
| API | Container App, CPU, **`api`** image | min 1 | ~$15–40/mo |
| Queue | Container App running `redis:7-alpine` (internal) | min 1 | negligible |
| GPU worker | Container App on the **`gpu-t4`** profile, **`worker`** image, 4 vCPU / **16 Gi** | **min 0** | GPU per-second while processing, ≈$0 idle |
| Vectors | **pgvector** in Supabase — reuses the existing DB, no separate vector service | — | $0 |

> **Vector-store env:** pgvector needs none — it reuses the Supabase connection. There are no
> `QDRANT_*` vars ([Vector Store](#vector-store--pgvector-supabase)).

Setting `REDIS_URL` on the API is the single switch that flips dispatch from in-process to ARQ offload,
so **the worker must already be consuming the queue before you set it**, or jobs queue unprocessed.

### Verifying the split works

1. Trigger an AI run (upload, or `POST /api/brain/reprocess/deployment/{id}`). API logs should show **`job_enqueued_arq`** (not `job_enqueued_local`).
2. `az containerapp replica list --name ww-embedding-worker-dev -g WW-AE` shows the worker scaling **0 → 1**.
3. Worker logs show `arq_worker_startup` then `auto_embed_complete` with a cluster count.
4. Annotations → **Group → Cluster (embeddings)** populates; the worker scales back to **0** when idle.

> Status polling already works cross-process: `recover_stuck_jobs()` + the Supabase `api_jobs` mirror mean the API reports progress on jobs the worker ran. No extra wiring needed.

---

## Troubleshooting

### Common Issues

**App won't start: `ValidationError` on startup**

Missing required environment variables. Check that `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY` are set.

```bash
# Verify env vars are loaded
az containerapp show \
  --name ww-backend \
  --resource-group WW-AE \
  --query "properties.template.containers[0].env"
```

**Jobs stuck in `queued` status**

With `REDIS_URL` **unset**, jobs run in-process as asyncio background tasks; with it set, they are
offloaded to the ARQ worker — so first check *which* process should have run the job. A job stuck in
`queued` on a `REDIS_URL`-configured API usually means the worker isn't running or KEDA never woke it
(see [GPU Worker](#gpu-worker--scale-to-zero-the-ml-worker)). If a container restarts mid-job, the job
store's `recover_stuck_jobs()` marks interrupted jobs as `failed` on next boot. Check restart logs:

```bash
az containerapp logs show \
  --name ww-backend \
  --resource-group WW-AE \
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
