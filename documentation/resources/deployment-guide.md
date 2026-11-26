# Deployment Guide

How to deploy the Wildlife Watcher V2 platform (backend + frontend) to production.

> **AWS:** a move to AWS, with the GPU work as batches on the REANNZ cluster, is built behind settings whose defaults keep the Azure path ([AWS and the REANNZ batch](#aws-and-the-reannz-batch)). Everything else below describes the Azure deployment the sites use. Status, design and the AWS deploy steps: [2026-10_aws-reannz-migration](../development%20reports/2026-10_aws-reannz-migration/README.md); signing in to AWS: [cloud-infrastructure.md](cloud-infrastructure.md#signing-in-to-aws).

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
| **Supabase** | Dev_Wildlife_Watcher ([project refs](cloud-infrastructure.md#supabase)) | Stag_Wildlife_Watcher |
| **Azure Container App** | `ww-backend-dev` (WW-AE RG) | `ww-backend` (WW-AE RG) |
| **Azure Blob Container** | `wildlife-watcher-uploads-dev` | `wildlife-watcher-uploads` (storage account `wwuploadsae`) |
| **CamtrapDP export ZIPs** | `camtrapdp-exports`, shared with staging | `camtrapdp-exports` (same account) |
| **Frontend** | Cloudflare Pages build of `dev` (`dev.ww-website.pages.dev`); no other branch is built, see [Preview Deployments](#preview-deployments) | Cloudflare Pages (`ww-website.pages.dev` + `wildlifewatcher.ai`) |
| **Google Drive** | `Data/dev` ([folder IDs](cloud-infrastructure.md#google-drive)) | `Data/Production` |

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
  ├── FastAPI API Server (port 8000)                                       ("ww-embedding-worker[-dev]",
  ├── Job dispatch: ARQ when REDIS_URL is set, else in-process asyncio       --target worker, serverless T4)
  └── Supabase sync for job persistence (api_jobs table)
```

> **Note**: heavy ML work (SpeciesNet, BioCLIP, DINOv3) runs in a **separate ARQ GPU worker**, not in
> the API container, because the lean `--target api` image carries no ML deps. Each environment has its
> own worker (serverless T4, scale-to-zero): **dev since 2026-07-03, production since 2026-07-26**.
> With `REDIS_URL` unset, `dispatch.py` falls back to running jobs in-process.
> Live state: [cloud-infrastructure.md](cloud-infrastructure.md) · how the prod worker was built:
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

The frontend is a static React+Vite app deployed to **Cloudflare Pages**, which builds `main` and `dev` only ([Preview Deployments](#preview-deployments)).

### Setup (One-Time)

1. Go to [Cloudflare Dashboard](https://dash.cloudflare.com/) → **Pages** → **Create a project** → **Connect to Git**
2. Select the `wildlifeai/ww-website` repository
3. Configure build settings:

   | Setting | Value |
   |---------|-------|
   | **Build command** | `cd frontend && npm install && npm run build` |
   | **Build output directory** | `frontend/dist` |
   | **Root directory** | `/` (repository root) |
   | **Node.js version** | `22` (match [00-GETTING-STARTED](../onboarding/00-GETTING-STARTED.md#prerequisites) and CI) |

4. Set environment variables (in Cloudflare Pages settings):

   | Variable | Value |
   |----------|-------|
   | `SUPABASE_URL` | `https://nuhwmubvygxyddkycmpa.supabase.co` |
   | `SUPABASE_ANON_KEY` | _(from Supabase Dashboard)_ |
   | `VITE_API_BASE_URL` | the production API's base URL, `https://` and its FQDN; the dev preview takes the dev API's ([base URLs](api-reference.md#wildlife-watcher-api-reference), checked against [cloud-infrastructure.md](cloud-infrastructure.md#azure)) |

5. Assign custom domain: `wildlifewatcher.ai` (DNS is already on Cloudflare)

### Preview Deployments

Which branches Cloudflare builds is the project's branch control, under **Settings → Builds &
deployments** ([Cloudflare's docs](https://developers.cloudflare.com/pages/configuration/branch-build-controls/)).
On 26 Oct 2026 it built `main` (production) and `dev` (preview,
`https://dev.ww-website.pages.dev`) and nothing else: none of the 32 feature and Dependabot pull
request heads checked, back to June 2026, has a Cloudflare build. Each build reports as a
`Cloudflare Pages` check run on its commit, with the build's own URL
(`https://<id>.ww-website.pages.dev`) and the branch alias in its summary, and creates no GitHub
deployment. Cloudflare never builds a pull request from a fork.

To build every other branch of this repo, and so give each pull request a preview and an E2E run
([`e2e/README.md`](../../e2e/README.md#in-ci)): **Preview branch: Custom branches**, **Include
Preview branches** `*`, **Exclude Preview branches** `dependabot/*`. The cost is builds: dev and
main had 144 pushes, each one built, in the 30 days to 26 Oct 2026, and pull requests outside
Dependabot had 204 `ci.yml` runs in the same days, each one a push Cloudflare would build. The
Free plan allows 500 builds a month, one at a time, so a pull request's build can hold up a dev
build ([limits](https://developers.cloudflare.com/pages/platform/limits/)).

---

## Environment Variables

See [00-GETTING-STARTED.md](../onboarding/00-GETTING-STARTED.md#environment-variables) for the complete variable reference.

**Critical production checklist:**

- [ ] `SUPABASE_SERVICE_ROLE_KEY` is set and kept secret
- [ ] `ALLOWED_ORIGINS` is set to your exact frontend domain(s)
- [ ] `SENTRY_DSN` is set for error tracking
- [ ] `LOG_LEVEL` is `info` (not `debug`)
- [ ] `AZURE_STORAGE_CONNECTION_STRING` is set for image buffering and the CamtrapDP export ZIPs
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

**Original-image storage**: *missing → uploads "complete" but produce no usable media; `GET /api/media/:id/image` answers **404** ("the file path cannot be resolved to a hosted image"). On AWS the bucket does this job: [AWS and the REANNZ batch](#aws-and-the-reannz-batch).*

| Var | Enables |
|-----|---------|
| `AZURE_STORAGE_CONNECTION_STRING` (+ `AZURE_STORAGE_CONTAINER_NAME`) | Blob buffering of originals. **This is the one that bites.** The container *name* alone does nothing; without the **connection string** every upload logs `azure_storage_not_configured_for_store` and no media rows are created. It also stores the CamtrapDP export ZIPs (`CAMTRAPDP_EXPORT_CONTAINER`); on Azure, without it the export answers `FEATURE_DISABLED`. |
| `SUPABASE_MEDIA_BUCKET` | Supabase Storage bucket for media. |
| `GOOGLE_DRIVE_ENABLED=true` + `GOOGLE_SERVICE_ACCOUNT_JSON` + `GOOGLE_DRIVE_FOLDER_ID` | Drive archival. **Folder ID alone does nothing** — without `ENABLED` + the service account, queued uploads stay `pending_drive_uploads` and the images never become servable. |

**AI pipeline** — *missing → "No animals detected"; no `speciesnet`/`pipeline` log lines:*

| Var | Enables |
|-----|---------|
| `FF_PIPELINE_ENABLED=true` | Inference endpoints. |
| `FF_SPECIESNET_ENABLED=true` (+ `SPECIESNET_RUN_MODE`) | SpeciesNet detector+classifier. **Runs in the ARQ worker, not the API image (`--target api`)** — confirm the worker container is deployed with the same flags + `REDIS_URL` + GPU. |
| `FF_MEDIA_REGISTRY_ENABLED=true` | Thumbnails / animal crops (the **Labels** view is empty without crops). |
| `FF_BIOCLIP_ENABLED`, `FF_WILDLIFE_BRAIN_ENABLED` (+ `HF_TOKEN` for the gated DINOv3 weights, `EMBEDDING_*`; the vector store is **pgvector** in Supabase — no `QDRANT_*` vars, Qdrant is removed) | BioCLIP + DINOv3 embeddings / clustering. See [Vector Store](#vector-store--pgvector-supabase) and [Wildlife Brain on a deployed worker](#wildlife-brain-on-a-deployed-worker). |
| `FF_MODEL_TRAINING_ENABLED` (+ `EDGE_IMPULSE_API_KEY`, `EDGE_IMPULSE_PROJECT_ID`) | Species Brain training from the Media page. The job runs on the **worker** when `REDIS_URL` is set, so set all three there as well as on the API; without the credentials the action only exports a dataset ZIP. Keep it off on AWS until training runs on the REANNZ cluster ([#463](https://github.com/wildlifeai/ww-website/issues/463)). |
| `FF_PER_CROP_CLASSIFY_ENABLED` | Per-detection (per-crop) species — one observation per animal, BioCLIP refines each crop. **Requires the GPU worker**; default off (collapses per image when off). |

**Demo account** — *missing → "Try the demo" self-disables (`DEMO_DISABLED`):*

| Var | Notes |
|-----|-------|
| `DEMO_EMAIL`, `DEMO_PASSWORD` | Must match the seeded demo user (`DEMO_PASSWORD` = dev `SEED_USER_PASSWORD`). See [demo-account.md](./demo-account.md). |

**Integrations (optional)**

| Var | Enables |
|-----|---------|
| `FF_INAT_ENABLED` + `INAT_TOKEN_KEY` (the optional OAuth app adds `INAT_CLIENT_ID`, `INAT_CLIENT_SECRET`, `INAT_REDIRECT_URI`) | iNaturalist publish and sync, on the API only. Steps: [iNaturalist](#inaturalist). |
| `EMAIL_PROVIDER`, `EMAIL_FROM`, `RESEND_API_KEY` / `SENDGRID_API_KEY` / `ACS_CONNECTION_STRING` | Notification email. |
| `SENTRY_DSN`, `LOG_LEVEL` | Error tracking / log verbosity. |

> **Quick audit** of a container's current env:
> ```bash
> az containerapp show --name ww-backend-dev -g WW-AE \
>   --query "properties.template.containers[0].env[].name" -o tsv | sort
> ```
> If `GOOGLE_DRIVE_ENABLED`, `FF_PIPELINE_ENABLED`, `FF_SPECIESNET_ENABLED`, or `FF_MEDIA_REGISTRY_ENABLED` are absent, that subsystem is off regardless of what the UI seems to show.

### AWS and the REANNZ batch

The settings of the AWS path, checked against `backend/app/config.py` on 2026-11-26. Every default
keeps the Azure path. On AWS the SAM template (`infra/aws/template.yaml`) sets the backends, the bucket
and the function names; every other value, secret or not, is an SSM SecureString under `/ww/<env>/`,
which `backend/app/aws_params.py` loads at each cold start. Status and design:
[2026-10_aws-reannz-migration](../development%20reports/2026-10_aws-reannz-migration/README.md).

| Var | Default | On AWS | What |
|---|---|---|---|
| `OBJECT_STORE_BACKEND` | `azure` | `s3` | Where the upload buffer, the export ZIPs and the batch results parts live: `azure` (Blob), `s3`, or `local` (a directory, for dev and tests) |
| `ARCHIVE_BACKEND` | `gdrive` | `s3` | Where the upload job archives originals: Drive (`GOOGLE_DRIVE_*`), or `originals/` in `S3_BUCKET` as `s3://` paths |
| `S3_BUCKET`, `S3_REGION` | empty | the stack's bucket and region | The bucket of both `s3` backends, and the only bucket `s3://` originals are read from, so an Azure API that serves them sets it too; an empty region is the SDK's default |
| `S3_PRESIGN_GET_SECONDS` | 300 | | Lifetime of a presigned GET handed to a browser or the cluster, 30 to 3,600 |
| `EXPORT_LINK_SECRET` | empty | SSM | HMAC key of the export download route ([API reference](api-reference.md#camtrapdp-export)) on `s3`. Empty, an export link is a plain presigned GET, which dies with the Lambda role's session |
| `LOCAL_OBJECT_STORE_DIR` | empty, `<system temp>/ww_objects` | | The `local` backend's directory |
| `WORKER_BACKEND` | `local` | `lambda` | `local`: ARQ when `REDIS_URL` is set, else in-process. `lambda`: the jobs in `BATCH_JOBS` (`backend/app/jobs/dispatch.py`) wait in `api_jobs` for the REANNZ batch, and every other job goes to `CPU_FUNCTION_NAME` |
| `CPU_FUNCTION_NAME` | empty, the job runs in-process | `ww-<env>-cpu` | The Lambda function for CPU jobs |
| `WORKER_TOKEN_SHA256` | empty, `/api/worker/*` answers 404 | SSM | Hex SHA-256 of the cluster's lease token |
| `GPU_IMAGE` | empty | SSM, written by `deploy-aws.yml` | The GPU image (`ghcr.io/...:<sha>`) that `GET /api/worker/pending` tells the cluster to run |
| `BATCH_MIN_PHOTOS`, `BATCH_MAX_WAIT_MINUTES` | 100, 60 | | The submit gate: the cluster is asked to run once this many photos wait, or once the oldest batch job is this old |
| `BATCH_CRON_INTERVAL_MINUTES` | 240, matching the 4-hourly crontab | | How often the cluster's crontab asks for work; must match it (`cluster/reannz/ww_cron.sh`). Added to the gate's wait for the wait the website states ([`batch_wait`](api-reference.md#jobs-async)) |
| `BATCH_MAX_QUEUE_HOURS` | 24 | | How long the reaper spares a queued batch job |
| `BATCH_LEASE_MINUTES`, `BATCH_MAX_ATTEMPTS` | 30, 2 | | A lease with no heartbeat for this long is requeued; leases a job may lose before it fails |
| `BATCH_LEASE_MAX_PHOTOS`, `BATCH_PART_MAX_PHOTOS` | 2,000, 500 | | Photos per lease and per results part |
| `WW_LAMBDA_ROLE` | empty | `api` or `cpu`, set by the template | Which function this process is: mounts `/events` and the flush middleware, skips the lifespan reaper |
| `WW_SSM_PREFIX`, `WW_ENV` | unset | `/ww/<env>/`, `<env>`, set by the template | Environment variables, not settings: the SSM path `aws_params.py` loads before `Settings()`, and the environment's name on the tick's metric line |
| `SUPABASE_*` | empty | SSM | Empty defaults let the GPU image import the app; the API refuses to start without them (`Settings.require_supabase`) |

### iNaturalist

Publishing photos to a person's own iNaturalist account, and the badge sync (#123). Off everywhere
until `FF_INAT_ENABLED` is set; switch it on in dev first. What publish and sync do:
[inaturalist-integration](../development%20reports/inaturalist-integration.md).

A person connects by pasting their personal API token from
<https://www.inaturalist.org/users/api_token> into Toolkit, *Connect* (`POST /api/inat/token`). That
is the normal way and needs no iNaturalist app. The token lasts about 24 hours; then the account
shows as not connected and the person pastes a new one. Signing in through an iNaturalist OAuth app
is an optional second path ([below](#optional-an-oauth-app)).

**1. Set the variables on the API container only.** The worker needs none: publish runs in API
requests, in rounds that each end inside the ingress timeout, and the sync, `sync_inat_job`, is a job
that runs in the API process (`IN_PROCESS_JOBS`). On AWS the sync runs on the `cpu` function, which
reads the same `INAT_*` parameters from SSM.

| Var | Value |
|---|---|
| `FF_INAT_ENABLED` | `true` |
| `INAT_TOKEN_KEY` | any random value of 32 characters or more, kept secret: a container secret (`secretref:`) on Azure, the SSM SecureString `/ww/<env>/INAT_TOKEN_KEY` on AWS. It encrypts every stored token: missing or shorter than 32 characters, the routes that connect or use an account answer `503` with the reason, and changing it disconnects everyone. While it is unset, `INAT_CLIENT_SECRET` keys the tokens in its place (`config.py::Settings.inat_token_key_name`); to move such a server to `INAT_TOKEN_KEY` without disconnecting anyone, give it the same value |

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"   # one key per environment
az containerapp secret set --name ww-backend-dev --resource-group WW-AE \
  --secrets inat-token-key=<the key>
az containerapp update --name ww-backend-dev --resource-group WW-AE \
  --set-env-vars FF_INAT_ENABLED=true INAT_TOKEN_KEY=secretref:inat-token-key
```

The deploy workflow merges its own variables, so these survive the next deploy. The new revision's
log has an `inat_settings_problem` line for anything still wrong (`services/inat_oauth.py`). The
frontend needs nothing: it asks `/api/inat/status`.

**2. Test on dev**, as a project member, on a deployment with a location and labelled photos:

1. Toolkit, *Connect*, paste a token from <https://www.inaturalist.org/users/api_token>: Toolkit
   shows *Connected as* your login.
2. Cluster Review, select a few photos labelled as an animal plus one marked Empty and one with a
   person, *Publish to iNaturalist*: one observation per encounter, location obscured, the two
   skipped, and the grid's dove badge on the published ones.
3. Publish 100 or more photos of one deployment: the network tab shows several
   `POST /api/inat/publish`, each under 30 seconds, while the button counts the photos reached,
   then one notice; no encounter is split into two observations on iNaturalist.
4. Publish the same selection again: every photo is skipped as already on iNaturalist.
5. Settings, *Sync now*: `POST /api/inat/sync` answers a job id at once, the panel shows its
   progress and then the counts, and the badge follows the quality grade. No identification
   comes back ([what happens today](../development%20reports/inaturalist-integration.md#what-happens-today-checked-25-oct-2026-123)).
6. Delete the test observations on iNaturalist.

Before production, check the three tables exist there (read-only):
`select to_regclass('public.inat_observations'), to_regclass('public.inat_observation_media'), to_regclass('public.inat_tokens');`
They exist on dev (checked 25 Oct 2026).

#### Optional: an OAuth app

`GET /api/inat/auth` and its callback sign a person in through an iNaturalist OAuth app instead.
The website has no button for it: Settings, Integrations links to the Toolkit paste box (26 Nov
2026). Without `INAT_CLIENT_ID` the route answers `500 INAT_CLIENT_ID not configured`; the startup
log does not report a missing app, only a redirect URI that is wrong for one.

**Register one app per environment** at <https://www.inaturalist.org/oauth/applications/new>, signed
in as the iNaturalist account that will own it, confidential if the form asks. Its redirect URI is
the API's `/api/inat/callback`, not the site, and `INAT_REDIRECT_URI` must be the same string:

| Environment | Redirect URI and `INAT_REDIRECT_URI` |
|---|---|
| dev (`ww-backend-dev`) | `https://ww-backend-dev.bravesand-8bd2f1d4.australiaeast.azurecontainerapps.io/api/inat/callback` |
| production (`ww-backend`, wildlifewatcher.ai) | `https://ww-backend.bravesand-8bd2f1d4.australiaeast.azurecontainerapps.io/api/inat/callback` |
| local | `http://localhost:8000/api/inat/callback` |

The code default, `https://wildlifewatcher.ai/inat/callback`, is the site, where no page handles the
callback, so set the variable everywhere. After the callback the API sends the browser to the first
`ALLOWED_ORIGINS` entry plus `/toolkit?inat=connected`, so on dev that entry must be the dev site.

**Add its three variables to the API**: `INAT_CLIENT_ID` (the app's ID), `INAT_CLIENT_SECRET` (the
app's secret, as a `secretref:`; with `INAT_TOKEN_KEY` set it keys no stored token) and
`INAT_REDIRECT_URI` (from the table above).

```bash
az containerapp secret set --name ww-backend-dev --resource-group WW-AE \
  --secrets inat-client-secret=<the app's secret>
az containerapp update --name ww-backend-dev --resource-group WW-AE \
  --set-env-vars INAT_CLIENT_ID=<the app's ID> INAT_CLIENT_SECRET=secretref:inat-client-secret \
    INAT_REDIRECT_URI=https://ww-backend-dev.bravesand-8bd2f1d4.australiaeast.azurecontainerapps.io/api/inat/callback
```

**Test**: signed in, call `GET /api/inat/auth` and open its `authorization_url`: iNaturalist asks
to authorise, then Toolkit shows *Connected as* your login.

> **Known limit:** the OAuth state lives in the memory of the API replica that started the sign-in
> (`routers/inaturalist.py::_pending_oauth`). A callback that reaches another replica, or one that
> restarted, answers `400 Invalid or expired OAuth state`. Dev runs one replica; production can run
> more ([replica counts](cloud-infrastructure.md#azure)). Connecting with a pasted token does not use it.

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
| `devices` | CamtrapDP import (device lookup and insert) | RLS + service-role |
| `deployments` | Most domains (authz, CamtrapDP import, events, soft delete) | RLS + service-role |
| `ai_models` | Model domain (register/update) | RLS + service-role |
| `ai_model_families` | Model domain (family→firmware ID mapping) | RLS + service-role |
| `firmware` | Manifest domain (config firmware lookup) | RLS + service-role |
| `user_roles` | Dependencies (permission checks) | RLS + service-role |
| `api_jobs` | Job system (status persistence + recovery) | service-role only |

### RPC Functions

| Function | Purpose |
|----------|---------|
| `check_user_uploader_role(p_user_id, p_org_id)` | Verifies user has upload permission |

### Realtime

The website's Realtime page (`/field`) listens for inserts on `lorawan_messages` and
`lorawan_parsed_messages`. ww-backend's migration `20260711043000_lorawan_realtime_publication.sql`
adds both to the `supabase_realtime` publication; nothing is set by hand.

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

**As built** (schema owned by [`ww-backend`](https://github.com/wildlifeai/ww-backend)):
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

> The heavy ML inference (DINOv3/SpeciesNet on GPU) is a separate concern. In both cloud environments
> it runs on the ARQ GPU worker (see [Scaling](#scaling)); locally it is the `gpu` profile's
> `embedding-worker` in `docker-compose.yml`, which gets jobs only when the API has `REDIS_URL` set.

---

## CI/CD Pipeline

### Backend: GitHub Actions → ACR → Azure Container App

The workflow `.github/workflows/deploy-backend.yml` triggers on pushes to `dev` and `main` that touch
what the images are built from: `backend/app/`, `backend/requirements*.txt`, `backend/Dockerfile`,
`backend/.dockerignore` or the workflow itself. A frontend, docs or test push does not rebuild or
redeploy anything; `workflow_dispatch` forces a run. The filter exists because every push used to
rebuild the worker image in the registry, about 12 minutes of paid build time each (NZ$34 in
October 2026).

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
| `DEMO_EMAIL`, `DEMO_PASSWORD` | Set on the API at each deploy, for *Try the demo* ([demo-account.md](demo-account.md)) |
| `HF_TOKEN` | Synced to the worker's `hf-token` secret for the gated DINOv3 weights; the workflow warns when it is missing |

### AWS: `deploy-aws.yml`

`.github/workflows/deploy-aws.yml` builds the `lambda` and `gpu` images and deploys `ww-<env>` with SAM.
It runs only by `workflow_dispatch`, from a workflow on `main`, when the repository variable
`AWS_DEPLOY_ENABLED` is `true`, with the GitHub environment's OIDC role `AWS_DEPLOY_ROLE_ARN`
(environments `dev` and `staging`). Its variables and the first deploy from a workstation:
[2026-10_aws-reannz-migration](../development%20reports/2026-10_aws-reannz-migration/README.md#setting-up-step-1-and-step-4-by-hand).

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
| `max-replicas` | 1 | see [cloud-infrastructure.md](cloud-infrastructure.md#azure) |
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

### Wildlife Brain on a deployed worker

`FF_WILDLIFE_BRAIN_ENABLED` defaults to `true` (#344), but an app that sets the variable keeps its
value, so check the API and the worker (`scripts/parity_audit.py` prints the `FF_*` values). The
embed step runs on the worker, after the pipeline, and needs:

| Setting | Why |
|---|---|
| `FF_WILDLIFE_BRAIN_ENABLED=true` on the API and the worker | The API mounts `/api/brain`; the worker runs the embed after each annotation job |
| `HF_TOKEN` (`secretref:hf-token`) | `dinov3-vith` weights are gated on HuggingFace. The token's account must have accepted access to `facebook/dinov3-vith16plus-pretrain-lvd1689m`. `deploy-backend.yml` syncs the secret from the `HF_TOKEN` GitHub secret and warns when it is missing |
| `EMBEDDING_DEVICE=cuda` | The code default is `cpu`; the `gpu-t4` worker sets `cuda` |
| `EMBEDDING_DEFAULT_MODEL` unset or `dinov3-vith` | 1280-d vectors; `dinov3-vits` is a different vector space |
| `FF_MEDIA_REGISTRY_ENABLED=true` | The embed reads the animal crops the pipeline writes |
| `--memory 16Gi` | ViT-H loads next to SpeciesNet and BioCLIP |

A worker missing the ML stack, the token or the GPU logs `auto_embed_skipped` with the reason and
writes no `embedding_runs` row; an explicit `POST /api/brain/embed` job fails with that reason.
The embed writes `embedding_runs`, `media_embeddings` and `cluster_assignments` with the service
role, so those tables need ww-backend's service-role grants: a missing grant fails every run with
`permission denied for table embedding_runs` (42501) in `auto_embed_failed`. Change a worker variable
with `az containerapp revision copy -n <worker> -g WW-AE --set-env-vars ...`, never `update` (it has
blanked the KEDA query), then run the runbook's
[verification](prod-worker-provisioning-runbook.md#verification).

**Run time and cost per deployment.** Only photos with an animal crop are embedded, so count
`media_assets.animal_crop_url` for the deployment, not its photos. Then:

```text
embed seconds = t_load + crops x seconds_per_frame
cost          = embed seconds x the gpu-t4 per-second rate
```

`seconds_per_frame` and `duration_seconds` are on the worker's `auto_embed_complete` log line
(#171). `t_load` is the weights download and load on a fresh replica: the worker scales to zero, so
a cold run pays it again. Neither is measured on the T4 yet, nor is the per-second rate (the
[evidence pipeline report](../development%20reports/2026-09_evidence-pipeline-architecture/README.md)
lists it as unknown). Measure one deployment with a known crop count on staging and take the rate
from Cost Management for that hour. The `POST /api/brain/reprocess/all` dry run uses placeholder
constants (75 ms per image, US$3 an hour in `embedding_lifecycle.py`), not measurements. The
[GCP pilot report §6.2](../development%20reports/_archive/2026-09_gcp-pilot-and-migration/README.md#62-the-worker-cost-per-photo)
has the same method worked through for the whole pipeline.

### Verifying the split works

1. Trigger an AI run (upload, or `POST /api/brain/reprocess/deployment/{id}`). API logs should show **`job_enqueued_arq`** (not `job_enqueued_local`).
2. `az containerapp replica list --name ww-embedding-worker-dev -g WW-AE` shows the worker scaling **0 → 1**.
3. Worker logs show `arq_worker_startup` then `auto_embed_complete` with a cluster count.
4. Annotations → **Group → Cluster (embeddings)** populates; the worker scales back to **0** when idle.

> Status polling already works cross-process: the Supabase `api_jobs` mirror means the API reports progress on jobs the worker ran, and the age-based reaper (`reap_stale_jobs`) fails a job that stops reporting. No extra wiring needed.

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
offloaded to the ARQ worker, so first check *which* process should have run the job. The jobs in
`IN_PROCESS_JOBS` always run in the API process, where one can be cut off when the replica scales in
(dev scales to zero); which jobs those are: [03-DATA-AND-SYNC](../onboarding/03-DATA-AND-SYNC.md#async-job-system). A job stuck in
`queued` on a `REDIS_URL`-configured API usually means the worker isn't running or KEDA never woke it
(see [GPU Worker](#gpu-worker--scale-to-zero-the-ml-worker)). Nothing is failed at boot: if a container
restarts mid-job, the age-based reaper (`reap_stale_jobs`, `backend/app/jobs/store.py`) fails the job once
it has stopped reporting. Check restart logs:

```bash
az containerapp logs show \
  --name ww-backend \
  --resource-group WW-AE \
  --type system
```

**Model conversion fails: "Vela command not found"**

The `ethos-u-vela` package needs to be installed in the container. Verify it's in `requirements.txt` and the Docker image was rebuilt.

**Rate limiting is too aggressive**

The limits are per route, in code (`@limiter.limit`); no setting changes them. The
list: [API reference, Rate Limiting](api-reference.md#rate-limiting).

**CORS errors from frontend**

Add your frontend origin to `ALLOWED_ORIGINS`:

```
ALLOWED_ORIGINS=https://wildlifewatcher.ai,http://localhost:5173
```
