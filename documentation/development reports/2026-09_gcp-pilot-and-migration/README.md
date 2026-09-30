# Google Cloud pilot and migration plan

> **Status:** 🔧 Active. Proposal 2026-09-26, decisions updated 2026-09-30; project `ww-pilot-dev`
> set up 2026-10-01 (§2, "State"); no code changed. Track 1, the ML-worker pilot, is due **16 October 2026** for OKR G3
> "Cloud cost per photo processed and stored, measured on a Google Cloud pilot", tracked in
> [#172](https://github.com/wildlifeai/ww-website/issues/172). Track 2, the
> full migration, follows the pilot number and credit approval. Prices are USD, read from the
> official pricing pages on 2026-09-26; NZD uses the ECB reference rate of 2026-09-25,
> **1 USD = 1.7634 NZD** (via `api.frankfurter.app`). Figures marked *unverified* must not be
> quoted onward. The Azure estate being replaced is
> [cloud-infrastructure.md](../../resources/cloud-infrastructure.md).

## 0. Decisions

| Topic | Decision, and why |
|---|---|
| Pilot scope | ML worker only (`--target worker`: SpeciesNet, BioCLIP, DINOv3) against the dev Supabase project, because G3 needs one cost-per-photo number by 16 Oct; native training is a later Track 2 step (stage 2.9) |
| Stays where it is | Supabase, Cloudflare (Pages, DNS), Google Drive and the existing GCP project `ww-drive-upload-photos`, because none of them is on the Azure subscription |
| Worker compute | Cloud Run **job**, 1 x L4, same image, because a job bills only while its task runs |
| Worker region | asia-southeast1 (Singapore), because Cloud Run has no L4 in Australia and asia-south1 is invitation only; us-central1 is 17% cheaper on the GPU SKU but farther from Supabase and Drive |
| Other regions | australia-southeast1 (Sydney) for the API, the upload buffer and everything else in Track 2 |
| Wake mechanism | The API starts one job execution per annotate run (Admin API `jobs.run`, job name and JSON arguments as container-argument overrides), because every push option has a deadline shorter than a run or an idle GPU tail (table below) |
| Redis and ARQ | Bypassed in the pilot and retired in Track 2 (stage 2.7), because `api_jobs` already holds job state; Memorystore Basic M1 is about US$47 a month plus a VPC and Direct VPC egress |
| Projects | `ww-pilot-dev` for the pilot; two projects `ww-dev` and `ww-prod` in Track 2, because they give clean billing lines and blast-radius separation |

Rejected wake options (sources in §7.1):

| Option | What rules it out |
|---|---|
| A. GPU service behind Cloud Tasks | `dispatchDeadline` max 30 min, so a 40-minute run is cancelled and retried; idle tail up to 600 s = US$0.21 per wake (§6.2) |
| B. GPU service behind Pub/Sub push | Ack deadline max 600 s |
| C. Cloud Scheduler tick polling `api_jobs` | Kept as the **fallback** if the API cannot call the Admin API: up to 1 min latency and a second component; 3 jobs free, then US$0.10 per job-month |
| D. GPU worker pool running ARQ | GPU worker pools cannot autoscale or scale to zero: about US$917 a month always on (§6.2) |
| **E. Job per annotate run (chosen)** | GPU task timeout 1 h, equal to ARQ's `job_timeout`; billed per execution; 1,000 running executions per project and region |

## 1. Resource map

Source: the inventory in
[cloud-infrastructure.md](../../resources/cloud-infrastructure.md#resources-ww-ae-rg-australiaeast).

| Azure (today) | Google Cloud (target) | Code or config change |
|---|---|---|
| `ww-env` Container Apps environment | None; the API is a Cloud Run service in australia-southeast1, the worker a job in asia-southeast1 | None |
| `wwregistry` (ACR, `weekly-purge` task) | Artifact Registry Docker repo `ww-backend`, asia-southeast1 (a second in australia-southeast1 for the API in Track 2); cleanup policy replaces the purge; tags `<sha>`, `dev-latest`, `latest`, `stable` kept | `deploy-backend.yml`; image `asia-southeast1-docker.pkg.dev/<project>/ww-backend/ww-backend-worker:<sha>` |
| `wwuploadsae` Blob (`wildlife-watcher-uploads`, `-dev`) | GCS buckets `ww-uploads`, `ww-uploads-dev`, australia-southeast1, Standard, uniform access, lifecycle delete after 1 day | Track 2: `services/object_storage.py` behind `UPLOAD_BUFFER_BACKEND=azure\|gcs` |
| `ww-redis-dev` (ARQ broker) | None (§0) | `jobs/dispatch.py` gains a third backend; `REDIS_URL` kept for the local `--profile gpu` stack |
| `ww-backend-dev` (dev API) | Cloud Run service `ww-backend-dev`, australia-southeast1, request-based billing, min 0, max 1 | Track 2; Cloudflare preview `VITE_API_BASE_URL` to the `run.app` URL |
| `ww-embedding-worker-dev` (T4, KEDA on `api_jobs`, `pg-conn`) | Cloud Run job `ww-ml-worker-dev`, asia-southeast1, 1 x L4 no zonal redundancy, 4 vCPU / 16 GiB, task timeout 3600 s, max retries 0 | Track 1; `jobs/cloudrun_entry.py` overrides the image `CMD` |
| `ww-backend` (prod API) | Cloud Run service `ww-backend`, australia-southeast1, request-based billing, min 1 | Track 2 cutover; prod gets its own Drive service account secret |
| `ww-kv-dev-ae` Key Vault (`ww-website-dotenv`) | Secret Manager: `ww-website-dotenv` for developers, one secret per runtime value | `scripts/fetch-env.sh`, `fetch-env.ps1` |
| Log Analytics | Cloud Logging; JSON stdout is parsed ([logging](https://docs.cloud.google.com/run/docs/logging)) | None |
| Alert `ww-gpu-worker-stuck-dev`, action group `ww-gpu-alerts` | Cloud Monitoring policy (execution running over 30 min) plus a Billing budget, which alerts only ([budgets](https://docs.cloud.google.com/billing/docs/how-to/budgets)); `--task-timeout 3600` is the hard stop | None |
| ACA secrets (`supabase-service-key`, `google-sa-json`, `hf-token`, `azure-storage-conn`, `pg-conn`, `acr-pw`) | Secret Manager via `--set-secrets`; runtime SA needs `roles/secretmanager.secretAccessor` ([secrets](https://docs.cloud.google.com/run/docs/configuring/services/secrets)) | `scripts/parity_audit.py` (§8) |
| GitHub secrets `ACR_LOGIN_SERVER`, `ACR_USERNAME`, `ACR_PASSWORD`, `AZURE_CREDENTIALS` | Workload Identity Federation; non-secret variables `GCP_PROJECT_ID`, `GCP_WIF_PROVIDER`, `GCP_DEPLOYER_SA` | `deploy-backend.yml` |
| GCP project `ww-drive-upload-photos` | Stays; its parent answers the organisation question (step 2); do not touch its service account | None |
| Supabase, Cloudflare Pages and DNS, Google Drive | Stay | Cloudflare env var and API CORS at cutover |

## 2. Track 1: the pilot

Owners: **Victor** (infrastructure, code), **Dinnie** (billing account, cost model, G3),
**Google CE** (credits, quota, architecture review).

**State, 2026-10-01.** Project `ww-pilot-dev` (number 762962404573) under organisation
`wildlife.ai` (963739238076), billed to `wildlife.ai_general` (015F09-37458C-7396FF, NZD).

| Step | State |
|---|---|
| 1 | No credits yet; US$2,000 requested from the accelerator ASM and mentor on 2026-10-01 |
| 2, 3, 6, 7 | Done. Step 3 also enabled `billingbudgets` and `serviceusage` (14 APIs). The 8 secrets exist, empty |
| 4 | Dataset `billing` (US) created; the Standard usage cost export is switched on in the Console only (still to do). Until credits land the budget is **NZ$50, credits excluded**, alerts at 50/80/100%; raise it to the credit amount then |
| 5 | Done except the throwaway-workflow check. GitHub variables `GCP_PROJECT_ID`, `GCP_WIF_PROVIDER`, `GCP_DEPLOYER_SA` set on the repository |
| 8 | Quota not checked yet |
| 9 | Code done (§8), in review |
| 10 to 15 | Not started |

| # | Step | Owner | Acceptance check |
|---|---|---|---|
| 1 | Confirm the credits and the billing account they land on; note its currency (an NZD account needs no conversion) | Dinnie + CE | Account shows the credit; Victor holds `Billing Account Administrator` or `Costs Manager` |
| 2 | Organisation: check with a `wildlife.ai` Workspace account whether an organisation exists and where `ww-drive-upload-photos` sits ([organisation docs](https://docs.cloud.google.com/resource-manager/docs/creating-managing-organization)); create `ww-pilot-dev` under it, link billing, label `env=dev,tier=pilot` | Victor | `gcloud projects describe ww-pilot-dev` shows parent and billing; `gcloud billing projects describe` agrees |
| 3 | Enable `run`, `artifactregistry`, `cloudbuild`, `secretmanager`, `iam`, `iamcredentials`, `sts`, `cloudresourcemanager`, `bigquery`, `logging`, `monitoring`, `cloudbilling` | Victor | `gcloud services list --enabled` lists all twelve |
| 4 | Billing export to BigQuery before any deploy: dataset `billing` (US multi-region backfills to the start of the previous month, a regional dataset exports only from enablement, [export](https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery)); Standard usage cost export ([setup](https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery-setup)); budget of the credit amount with 50/80/100% e-mail thresholds | Victor | `billing.gcp_billing_export_v1_<ACCOUNT_ID>` exists within a day; budget listed |
| 5 | IAM (table below) | Victor | A throwaway workflow with `google-github-actions/auth@v3` gets a token for `github-deployer@`; `gcloud projects get-iam-policy` matches |
| 6 | Artifact Registry `ww-backend` (Docker, asia-southeast1); cleanup keeps the 5 newest `<sha>` tags, never deletes `dev-latest`, `latest`, `stable` | Victor | `gcloud artifacts repositories describe ww-backend --location asia-southeast1` shows the policy |
| 7 | Secrets (§4.3 pilot list), automatic replication | Victor | `gcloud secrets list` shows them; only `ww-ml-worker@` can access them |
| 8 | GPU quota: the first GPU job in asia-southeast1 grants 3 `NvidiaL4GpuAllocNoZonalRedundancyPerProjectRegion` ([quotas](https://docs.cloud.google.com/run/quotas)); ask the CE about a raise and Singapore capacity | Victor + CE | Quotas page shows 3 or more |
| 9 | Code (§8), PR to `dev` | Victor | `ruff check`, `ruff format --check`, `pytest` green; API image builds without ML deps; `python -m app.jobs.cloudrun_entry selftest` exits 0 on the dev image |
| 10 | Build with Cloud Build (§4.1); create the job once by hand (command below) | Victor | `gcloud run jobs execute ww-ml-worker-dev --region asia-southeast1 --wait` succeeds; log shows `cuda? True`, three model loads and the time to "models ready" (`t_cold`, §6.2) |
| 11 | First real run (command below) after creating the `api_jobs` row; dev is reseeded without notice ([SKILL.md](../../../.agents/skills/SKILL.md)), so record ids as evidence only | Victor | `api_jobs.status = 'completed'`; new `observations` with `source_type='ai'`, `ai_origin='cloud'`; execution start and end noted |
| 12 | Switch the dev API and park the Azure worker (§3); three uploads of about 10, 100 and 500 photos; record job ids, media ids, counts, durations | Victor | API logs `job_dispatched_cloudrun`, not `job_enqueued_arq`; `az containerapp replica list` for the Azure worker stays empty; Cloud AI labels on all three |
| 13 | Two days after the last run: §2.1 and §2.2 queries; fill §6.4 and the G3 row of the OKR sheet | Victor (query), Dinnie (sheet) | Cost per photo in NZD with the window, the photo count and the query text |
| 14 | Architecture review with the CE (§7.4); Track 2 go/no-go | Victor + CE | Decisions recorded here; open items filed |
| 15 | Roll back (§3) or continue: delete the Azure dev worker and `ww-redis-dev` after two quiet weeks | Victor | Either the Azure worker processes an upload again, or both apps are gone and cloud-infrastructure.md says so |

IAM (step 5):

| Principal | Grant |
|---|---|
| Victor | `roles/owner` |
| Dinnie | `roles/billing.viewer`, `roles/bigquery.user` on the project |
| `ww-ml-worker@` (job runtime) | `roles/secretmanager.secretAccessor`, `roles/logging.logWriter` |
| `github-deployer@` | `roles/artifactregistry.writer`, `roles/cloudbuild.builds.editor`, `roles/run.developer`, `roles/logging.logWriter`, `roles/serviceusage.serviceUsageConsumer`; `roles/iam.serviceAccountUser` on `ww-ml-worker@` and on itself; `roles/storage.admin` on `gs://ww-pilot-dev-build-source` only. Builds run **as this account** (§4.1), because a new project's default build account has no rights to push or log |
| `ww-job-trigger@` | Created, no role and no key yet: `roles/run.developer` on the job once it exists (§4.2) |
| WIF pool `github`, OIDC provider `wildlifeai`, issuer `https://token.actions.githubusercontent.com` | Mapping `google.subject=assertion.sub, attribute.repository=assertion.repository, attribute.repository_owner=assertion.repository_owner`; condition `assertion.repository_owner == 'wildlifeai'`; `roles/iam.workloadIdentityUser` on `github-deployer@` for `principalSet://…/attribute.repository/wildlifeai/ww-website` ([WIF for pipelines](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines)) |
| Default compute service account | No Editor ([service identity](https://docs.cloud.google.com/run/docs/securing/service-identity)). The organisation sets no policy against the automatic grant, so enabling Cloud Run gave it `roles/editor`; removed by hand on 2026-10-01. Check again after enabling any API |

Steps 10 and 11 (flags from [jobs GPU](https://docs.cloud.google.com/run/docs/configuring/jobs/gpu)
and [execute jobs](https://docs.cloud.google.com/run/docs/execute/jobs)):

```bash
gcloud run jobs create ww-ml-worker-dev \
  --image asia-southeast1-docker.pkg.dev/ww-pilot-dev/ww-backend/ww-backend-worker:<sha> \
  --region asia-southeast1 --gpu 1 --gpu-type nvidia-l4 --no-gpu-zonal-redundancy \
  --cpu 4 --memory 16Gi --task-timeout 3600 --max-retries 0 --tasks 1 --parallelism 1 \
  --service-account ww-ml-worker@ww-pilot-dev.iam.gserviceaccount.com \
  --command python --args -m,app.jobs.cloudrun_entry,selftest \
  --set-secrets SUPABASE_URL=ww-dev-supabase-url:latest,SUPABASE_ANON_KEY=ww-dev-supabase-anon-key:latest,SUPABASE_SERVICE_ROLE_KEY=ww-dev-supabase-service-role-key:latest,HF_TOKEN=ww-hf-token:latest,GOOGLE_SERVICE_ACCOUNT_JSON=ww-dev-google-sa-json:latest,GOOGLE_DRIVE_FOLDER_ID=ww-dev-google-drive-folder-id:latest,GENERAL_ORG_ID=ww-dev-general-org-id:latest \
  --set-env-vars <the §4.2 flags, copied from the running Azure worker> \
  --labels env=dev,component=ml-worker,pilot=gcp-2026-10

gcloud run jobs execute ww-ml-worker-dev --region asia-southeast1 --wait \
  --args=-m,app.jobs.cloudrun_entry,annotate_deployments_job,'{"job_id":"<uuid>","deployment_ids":["<dev deployment>"],"force":true}'
```

### 2.1 The cost query

Schema and the `labels` / `credits` pattern:
[standard-usage](https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery-tables/standard-usage).

```sql
DECLARE window_start TIMESTAMP DEFAULT TIMESTAMP('2026-10-01 00:00:00+00');
DECLARE window_end   TIMESTAMP DEFAULT TIMESTAMP('2026-10-15 00:00:00+00');

SELECT
  service.description                     AS service,
  sku.description                         AS sku,
  ANY_VALUE(currency)                     AS currency,
  SUM(cost)                               AS gross_cost,
  SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS credits,
  SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS net_cost,
  SUM(usage.amount)                       AS usage_amount,
  ANY_VALUE(usage.unit)                   AS usage_unit
FROM `ww-pilot-dev.billing.gcp_billing_export_v1_XXXXXX_XXXXXX_XXXXXX`
LEFT JOIN UNNEST(labels) AS l ON l.key = 'component'
WHERE project.id = 'ww-pilot-dev'
  AND usage_start_time >= window_start AND usage_start_time < window_end
  AND (l.value = 'ml-worker' OR l.value IS NULL)   -- keep unlabelled rows: registry, secrets, logging
GROUP BY 1, 2
ORDER BY gross_cost DESC;
```

`gross_cost` is the number (what the credits pay); `net_cost` is what would be invoiced. The L4
SKU's `usage_amount` (seconds) cross-checks
`gcloud run jobs executions list --job ww-ml-worker-dev --region asia-southeast1 --format json`.
If job labels do not reach the export (§7.2), drop the label join: the project holds only the
worker.

### 2.2 How "photos processed" is counted

Columns checked against ww-backend `34_media.sql`, `35_observations.sql`, `33_api_jobs.sql` at
`987ec56` (2026-09-21).

```sql
-- Photos stored in the window (every uploaded photo goes through the pipeline)
select count(*) as photos_stored
from media
where created_at >= '2026-10-01' and created_at < '2026-10-15'
  and deleted_at is null;

-- Photos that received at least one Cloud AI observation in the window (cross-check)
select count(distinct media_id) as photos_with_cloud_ai
from observations
where source_type = 'ai' and ai_origin = 'cloud'
  and created_at >= '2026-10-01' and created_at < '2026-10-15'
  and deleted_at is null;

-- Pilot runs (one api_jobs row per execution)
select id, status, created_at, updated_at,
       job_data->>'label' as label, job_data->'deployment_ids' as deployment_ids
from api_jobs
where job_data->>'kind' = 'ai_pipeline'
  and created_at >= '2026-10-01' and created_at < '2026-10-15'
order by created_at;
```

| Rule | Why |
|---|---|
| Denominator is `photos_stored` | Blank frames may have no `observations` row ([04-AI-PIPELINE](../../onboarding/04-AI-PIPELINE.md)) |
| Bytes per photo from the `azure_blob_stored … size_bytes` log lines | `media` has no size column |
| Run the counts on upload day | A dev reseed deletes the rows |

## 3. Parking the Azure dev worker, and rolling back

Only one worker may serve the dev database during the pilot.

```bash
# Park. First, on ww-backend-dev: set CLOUD_RUN_JOB_NAME, CLOUD_RUN_JOB_REGION,
# GOOGLE_CLOUD_PROJECT and CLOUD_RUN_TRIGGER_SA_JSON (§4.2), then unset REDIS_URL.
az containerapp update -n ww-embedding-worker-dev -g WW-AE --min-replicas 0 --max-replicas 0
# Leave ww-redis-dev running until the verdict. deploy-backend.yml keeps rolling the
# 0-replica worker image, which is harmless.

# Roll back (minutes). On ww-backend-dev: REDIS_URL=redis://ww-redis-dev:6379, unset CLOUD_RUN_*.
az containerapp update -n ww-embedding-worker-dev -g WW-AE --max-replicas 1
az containerapp show -n ww-embedding-worker-dev -g WW-AE \
  --query "properties.template.scale.rules[0].custom.metadata.query"   # must not be empty
```

Hard stop: [runbook](../../resources/prod-worker-provisioning-runbook.md#cost--rollback);
the scaler: [deployment-guide](../../resources/deployment-guide.md#gpu-worker--scale-to-zero-the-ml-worker).

## 4. Draft workflow, config differences, secrets

### 4.1 Build and deploy

[`deploy-ml-worker-gcp.yml`](../../../.github/workflows/deploy-ml-worker-gcp.yml) signs in with
WIF as `github-deployer@`, builds the `worker` target with
[`backend/cloudbuild.worker.yaml`](../../../backend/cloudbuild.worker.yaml) (E2_HIGHCPU_32, 200 GB
disk, from the [GPU best practices](https://docs.cloud.google.com/run/docs/configuring/services/gpu-best-practices)
build example), changes only the job's image like Azure's `revision copy`, then runs the
self-test.

- It runs by hand; a push to `dev` deploys only when the repository variable
  `GCP_WORKER_AUTODEPLOY` is `true`, because every build uses the 32-CPU machine.
- The build runs as `github-deployer@` (`--service-account`) with source staged in
  `gs://ww-pilot-dev-build-source`. Its log is not streamed into the Actions run
  (`--suppress-logs`): streaming needs project Viewer on the deployer. The step still waits and
  fails with the build; read the log in Cloud Build history.
- Until the job exists (step 10) the roll and self-test steps are skipped with a notice.

### 4.2 Config differences from Azure

| Setting | Azure worker today | Cloud Run job |
|---|---|---|
| Entry point | `CMD ["arq", "app.jobs.worker.WorkerSettings"]` | `--command python --args -m,app.jobs.cloudrun_entry,<job>,<json>` per execution |
| `REDIS_URL` | `redis://ww-redis-dev:6379` | unset |
| Scaling | KEDA Postgres scaler, `pg-conn`, 15 min window, 0 to 1 | one execution per run |
| ARQ `job_timeout = 3600` | ARQ setting | `--task-timeout 3600` |
| ARQ `max_tries = 1` | ARQ setting | `--max-retries 0` |
| ARQ `_defer_by` 60 s debounce | ARQ setting | the API starts the execution 60 s later from a background task, so the `api_jobs` row stays queued for later chunks to reuse. An API restart inside that minute loses the start and the stale-job reaper fails the job after 60 min; Track 2 may use a Cloud Tasks task 60 s out |
| CPU / memory | 4 vCPU / 16 Gi (8 Gi OOM-killed DINOv3 ViT-H) | `--cpu 4 --memory 16Gi`, the L4 minimum (8 / 32 recommended) |
| Device flags | `EMBEDDING_DEVICE=cuda`, `BIOCLIP_DEVICE=cuda` | same |
| Feature flags | `FF_ML_ENABLED`, `FF_PIPELINE_ENABLED`, `FF_SPECIESNET_ENABLED`, `FF_BIOCLIP_ENABLED`, `FF_PER_CROP_CLASSIFY_ENABLED`, `FF_MEDIA_REGISTRY_ENABLED`, `FF_WILDLIFE_BRAIN_ENABLED`, `FF_EDGE_REFLECT_ENABLED`, `SPECIESNET_RUN_MODE=single_thread`, `EMBEDDING_DEFAULT_MODEL`, `EMBEDDING_BATCH_SIZE`, `SUPABASE_MEDIA_BUCKET`, `LOG_LEVEL` | same values, copied with `az containerapp show … env[].name`, set on the **job** |
| Drive | `GOOGLE_DRIVE_ENABLED`, `GOOGLE_DRIVE_FOLDER_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON` | same; originals are read from Drive |
| Blob buffer | `AZURE_STORAGE_CONNECTION_STRING` (unused by the annotate path) | not set |
| Model weights | downloaded at first use: SpeciesNet from Kaggle, BioCLIP and DINOv3 from Hugging Face (`HF_TOKEN`) | same, so every cold start pays the downloads (§7.3) |
| New on the **API** during the pilot | | `GOOGLE_CLOUD_PROJECT=ww-pilot-dev`, `CLOUD_RUN_JOB_NAME=ww-ml-worker-dev`, `CLOUD_RUN_JOB_REGION=asia-southeast1`, `CLOUD_RUN_TRIGGER_SA_JSON` (key for `ww-job-trigger@`, only `roles/run.developer` on that job; deleted at stage 2.2) |

### 4.3 Secrets that move to Secret Manager (names only)

| Scope | Secrets |
|---|---|
| Pilot (dev) | `ww-dev-supabase-url`, `ww-dev-supabase-anon-key`, `ww-dev-supabase-service-role-key`, `ww-hf-token`, `ww-dev-google-sa-json`, `ww-dev-google-drive-folder-id`, `ww-dev-general-org-id`, `ww-dev-sentry-dsn` (optional) |
| Track 2 adds | `ww-website-dotenv`, `ww-dev-demo-email`, `ww-dev-demo-password`, `ww-dev-lorawan-webhook-secret`, `ww-dev-lorawan-ttn-webhook-secret`, `ww-dev-lorawan-chirpstack-webhook-secret`, `ww-dev-inat-client-id`, `ww-dev-inat-client-secret`, and the `ww-prod-*` twins with a **separate** `ww-prod-google-sa-json` |
| No successor | `AZURE_STORAGE_CONNECTION_STRING`, `pg-conn` |

## 5. Track 2: the full migration

Rule: Supabase is the state, so every rollback is "point the variable or URL back"; nothing on
Azure is deleted before stage 2.8.

| Stage | What | Rollback |
|---|---|---|
| 2.0 Foundations | Projects `ww-dev` and `ww-prod` (or keep `ww-pilot-dev` as `ww-dev`), same org, billing account and WIF pool; Artifact Registry in australia-southeast1 for the `api` image; Secret Manager (§4.3); a budget per project | None needed |
| 2.1 Buffer | `services/object_storage.py` (`store_blob`, `retrieve_blob`, `delete_blob` over GCS) chosen by `UPLOAD_BUFFER_BACKEND`; drop the underscore stripping of metadata keys; deploy with `azure`, flip to `gcs` when no upload is running | Flip back after in-flight uploads finish |
| 2.2 Dev API | Service `ww-backend-dev` (§1) with service identity `ww-backend-dev@` holding `roles/run.developer` on the job, replacing the pilot key; Cloudflare preview `VITE_API_BASE_URL` to the `run.app` URL (needs a Pages redeploy); `ALLOWED_ORIGINS` unchanged; run the parity audit | Cloudflare env back to the Azure FQDN |
| 2.3 Observability | Alert on a job execution over 30 min; uptime check on `/health` for both APIs; budget thresholds per project | None |
| 2.4 Prod API | Service `ww-backend`, min 1, max 3, 0.5 vCPU / 1 GiB, request-based billing, prod Supabase secrets, `DEMO_*`, prod Drive service account split from dev; soak with the staging preview | Delete the service |
| 2.5 Prod cutover | (a) deploy freeze; (b) `ALLOWED_ORIGINS=https://wildlifewatcher.ai,https://ww-website.pages.dev` (read the Azure value with the audit first); (c) Pages **production** `VITE_API_BASE_URL` to the new URL and a production deploy, no DNS change; (d) verify `/health`, "Try the demo", one upload with AI, LoRaWAN webhooks re-pointed by hand on TTN and Chirpstack; (e) Azure prod app kept idle two weeks. Optional before (c): `api.wildlifewatcher.ai` CNAME to the Cloud Run custom-domain target | Pages env back and redeploy; webhook URLs back |
| 2.6 Prod worker | Same job definition with `ww-prod-*` secrets in the prod project, when prod has real traffic | `CLOUD_RUN_JOB_NAME` empty on the prod API (today's prod behaviour) |
| 2.7 Retire ARQ | Delete `worker.py`, `GPU_PENDING_KEY` and `arq` once no environment sets `REDIS_URL` | Git revert |
| 2.8 Azure teardown | After two quiet weeks: delete `WW-AE`; remove `ACR_*`, `AZURE_CREDENTIALS`, `azure-storage-blob`, `aiohttp`, the `AZURE_*` settings; rewrite the living docs (§8); recover or purge the soft-deleted `secrets-staging` vault | None: the point of no return |
| 2.9 Native training (later) | Species Brain training as a Cloud Run job, per the [native training report](../2026-09_gcp-native-model-training/README.md) (lands with `feat/gcp-native-training-v2`) and #151's [`species-brain-training-spec.md`](../species-brain-training-spec.md) (lands with `feat/species-brain-training`); model validation, including the 512 KB arena check, stays in `services/vela.py` for every model source | Leave `MODEL_TRAINER` on Edge Impulse |

## 6. Cost model

### 6.1 List prices read on 2026-09-26 (USD)

Cloud Run prices from <https://cloud.google.com/run/pricing>; Sydney and Singapore are Tier 2
(Singapore's L4 is exactly 1.2 x the us-central1 price). Billing rounds up to 100 ms.

| Item | Price | Notes |
|---|---|---|
| Cloud Run services, instance-based, Tier 2 (Sydney, Singapore): CPU | $0.0000216 per vCPU-second | Tier 1 (us-central1): $0.000018 |
| … memory | $0.0000024 per GiB-second | Tier 1: $0.000002 |
| … NVIDIA L4, no zonal redundancy, Singapore | **$0.00022404 per second** | Tier 1 us-central1: $0.0001867. With zonal redundancy: $0.00034908. GPU requires instance-based billing; "no per request fees" |
| Cloud Run **jobs**, Tier 2: CPU / memory / L4 | $0.0000216 / $0.0000024 / $0.00022404 | Jobs are no-zonal-redundancy only |
| Cloud Run services, request-based, Tier 2: CPU active / idle (min instance) | $0.0000336 / $0.0000035 per vCPU-second | Tier 1: $0.000024 / $0.0000025 |
| … memory active / idle | $0.0000035 / $0.0000035 per GiB-second | Tier 1: $0.0000025 both |
| … requests | $0.40 per million | 2 million free per month |
| Cloud Run worker pools, Tier 2: CPU / memory / L4 | $0.00001512 / $0.00000168 / $0.00022404 | Always on; GPU pools cannot autoscale |
| Free tier (per billing account, month, at us-central1 rates) | instance-based 240,000 vCPU-s + 450,000 GiB-s; request-based 180,000 vCPU-s + 360,000 GiB-s + 2 M requests | Applied as a Tier 1 spending discount |
| Artifact Registry storage | $0.000136986 per GiB-hour (about $0.10 per GiB-month) after 0.5 GiB free | <https://cloud.google.com/artifact-registry/pricing>; same-region transfer to Cloud Run free |
| Cloud Storage, Standard, Sydney | $0.023 per GiB-month; Class A $0.005 per 1,000, Class B $0.0004 per 1,000 | <https://cloud.google.com/storage/pricing> |
| Secret Manager | 6 active versions free, then $0.000082192 per version-hour (about $0.06 per version-month); 10,000 accesses free, then $0.03 per 10,000 | <https://cloud.google.com/secret-manager/pricing> |
| Memorystore for Redis, Sydney | Basic M1 $0.065 per GiB-hour ($47.45 per GiB-month); Standard M1 $0.114 | <https://cloud.google.com/memorystore/docs/redis/pricing> |
| Cloud Tasks | 1 million operations free, then $0.40 per million | <https://cloud.google.com/tasks/pricing> |
| Cloud Scheduler | 3 jobs free per billing account, then $0.10 per job per 31 days | <https://cloud.google.com/scheduler/pricing> |
| Pub/Sub | 10 GiB per month free, then $40 per TiB | <https://cloud.google.com/pubsub/pricing> |

### 6.2 The worker: cost per photo

L4 job in Singapore at 4 vCPU / 16 GiB:

```text
GPU     0.00022404
CPU     4  x 0.0000216 = 0.0000864
memory  16 x 0.0000024 = 0.0000384
total   0.00034884 USD/s   = NZ$0.000615/s   (US$1.256 / NZ$2.215 per hour)

at 8 vCPU / 32 GiB:        0.00047364 USD/s  (US$1.705 per hour)
us-central1, 4 / 16:       0.0002907  USD/s  (US$1.047 per hour)

cost per photo = 0.00034884 x (t_photo + t_cold / N)
```

Inference only, at the T4's 1 to 2 s per image (L4 speed *unverified* until step 10):

| Seconds per photo | USD per photo | NZD per photo | NZD per 1,000 photos |
|---|---|---|---|
| 1.0 | $0.000349 | NZ$0.000615 | NZ$0.62 |
| 1.5 | $0.000523 | NZ$0.000923 | NZ$0.92 |
| 2.0 | $0.000698 | NZ$0.00123 | NZ$1.23 |

With the fixed cost per execution (`t_cold`: image pull, three model downloads and loads, CUDA
warm-up; measured in step 10), `t_photo` = 1.5 s:

| Photos per run (N) | t_cold = 60 s | t_cold = 120 s | t_cold = 300 s |
|---|---|---|---|
| 10 | US$0.00262 (NZ$0.0046) | US$0.00471 (NZ$0.0083) | US$0.0110 (NZ$0.0194) |
| 100 | US$0.000732 (NZ$0.00129) | US$0.000942 (NZ$0.00166) | US$0.00157 (NZ$0.00277) |
| 1,000 | US$0.000544 (NZ$0.00096) | US$0.000565 (NZ$0.00100) | US$0.000628 (NZ$0.00111) |

Rejected shapes: GPU service idle tail 600 s x $0.00034884 = **US$0.209 (NZ$0.37) per wake**;
GPU worker pool always on 0.00034884 x 2,629,800 s = **about US$917 (NZ$1,618) per month**.

### 6.3 Everything else, monthly

Azure column from the 2026-07-28 cost review in
[cloud-infrastructure.md](../../resources/cloud-infrastructure.md), not re-measured since the
August cuts.

| Component | Azure (NZD/month) | Google Cloud (USD list, NZD at 1.7634) | Basis |
|---|---|---|---|
| Prod API, always warm | about NZ$55 to 60 | **US$13.81 (NZ$24.35)** idle at 0.5 vCPU / 1 GiB, request-based, min 1, plus active time (1 h a day about US$2.22); US$34.71 instance-based | (0.5 x 0.0000035 + 1 x 0.0000035) x 2,629,800 s |
| Dev API, scale to zero | about NZ$0 idle | about US$0 idle | request-based, min 0 |
| Redis broker | NZ$55 to 60 before right-sizing | **US$0** (bypassed); Memorystore would be US$47.45 (NZ$83.67) | §0 |
| Container registry | NZ$33 flat (45.7 GiB) | **about US$4.57 (NZ$8.06)** for 45.7 GiB, no SKU floor | 45.7 x 0.10 |
| Upload buffer | inside NZ$0.03 | cents: 5 GiB transient about US$0.12; about US$0.0000054 per photo written and read | GCS Sydney |
| Secrets | inside NZ$0.03 | about US$0.36 for 12 versions (6 free) | Secret Manager |
| Logs, alerting, budget, billing export | inside NZ$0.03 | *unverified*, expected inside free allotments | |
| GPU compute | NZ$25 to 30 of T4 spikes (no photo count, so no per-photo figure) | §6.2 per photo plus cold starts | |
| **Fixed floor** | **NZ$135 to 215** (NZ$212.84 measured, minus the expected NZ$78 of August cuts) | **about US$20 (NZ$35)** plus GPU seconds | |

### 6.4 Pilot result (step 13)

| Window | Executions | Photos stored | Photos with Cloud AI | GPU seconds (billing) | Gross cost | Cost per photo |
|---|---|---|---|---|---|---|
| | | | | | | |

Cost per photo processed and stored = §6.2 measured cost per photo + buffer cents + Supabase
plan / photos stored in the month (Supabase price *unverified*); originals stay in Drive, off the
Google Cloud bill. State each part separately.

## 7. Facts, unverified items, risks, CE questions

### 7.1 Verified facts (all read 2026-09-26)

| Fact | Source |
|---|---|
| L4 regions for Cloud Run services, jobs and worker pools: asia-southeast1, asia-south1 (invitation only), europe-west1, europe-west4, us-central1, us-east4. **No Australian region.** | [services GPU](https://docs.cloud.google.com/run/docs/configuring/services/gpu), [jobs GPU](https://docs.cloud.google.com/run/docs/configuring/jobs/gpu), [worker pools GPU](https://docs.cloud.google.com/run/docs/configuring/workerpools/gpu) |
| L4 minimum 4 CPU and 16 GiB (8 and 32 recommended); GPU services scale to zero; "GPU is billed for the entire duration of the instance lifecycle"; instance-based billing required for GPU; new projects get 3 L4 (no zonal redundancy) per region automatically; zonal redundancy defaults on for services, jobs are no-zonal-redundancy only | same pages, [quotas](https://docs.cloud.google.com/run/quotas) |
| Usage rounded up to 100 ms; GPU SKUs per second | [pricing](https://cloud.google.com/run/pricing) |
| Service request timeout default 5 min, max 60 min; job task timeout default 10 min, max 168 h, **1 h with a GPU** | [request-timeout](https://docs.cloud.google.com/run/docs/configuring/request-timeout), [task-timeout](https://docs.cloud.google.com/run/docs/configuring/task-timeout) |
| No direct limit on image size; 32 GiB memory and 8 vCPU per instance; startup timeout 4 min (services must listen, jobs must not) | [quotas](https://docs.cloud.google.com/run/quotas), [container contract](https://docs.cloud.google.com/run/docs/container-contract) |
| Idle instances kept "up to 15 minutes, or 10 minutes for GPUs"; scaling from zero only on a request | [about-instance-autoscaling](https://docs.cloud.google.com/run/docs/about-instance-autoscaling) |
| Worker pools: no endpoint, pull-based, manual or CPU / Pub/Sub-backlog autoscaling, no scale to zero, "GPU worker pools cannot be autoscaled", billed per instance duration | [resource model](https://docs.cloud.google.com/run/docs/resource-model), [deploy worker pools](https://docs.cloud.google.com/run/docs/deploy-worker-pools) |
| Jobs: up to 10,000 tasks per execution, 1,000 running executions per project and region, per-execution overrides for args, env, task count, timeout | [quotas](https://docs.cloud.google.com/run/quotas), [execute jobs](https://docs.cloud.google.com/run/docs/execute/jobs) |
| Cloud Tasks HTTP `dispatchDeadline` 15 s to 30 min (default 10 min); Pub/Sub ack deadline 10 s to 600 s | [tasks REST](https://docs.cloud.google.com/tasks/docs/reference/rest/v2/projects.locations.queues.tasks), [subscription properties](https://docs.cloud.google.com/pubsub/docs/subscription-properties) |
| GPU driver attach "approximately 5 seconds"; models under 10 GB may live in the image, larger in Cloud Storage; no published cold start for a multi-GB image | [GPU services](https://docs.cloud.google.com/run/docs/configuring/services/gpu), [GPU best practices](https://docs.cloud.google.com/run/docs/configuring/services/gpu-best-practices) |
| Billing export: standard usage cost table with `labels`, `project.labels`, `system_labels`, repeated `credits`; no backfill for regional datasets, previous-month backfill for multi-region | [export](https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery), [schema](https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery-tables/standard-usage) |
| Revision-level labels on services reach the billing export | [labels](https://docs.cloud.google.com/run/docs/configuring/labels) |
| Supabase shared pooler is IPv4 only; direct connections IPv6 unless the IPv4 add-on is bought; the worker uses the HTTP client, not the pooler | [Supabase connecting](https://supabase.com/docs/guides/database/connecting-to-postgres) |

### 7.2 Unverified (do not quote)

- Latency from Singapore to the dev Supabase project (Australia East) and to Drive.
- L4 seconds per image against the T4's 1 to 2 s; `t_cold` for the multi-GB worker image.
- Whether the image with baked weights stays under 10 GB.
- Prices of Cloud Build, Cloud Logging, BigQuery storage and queries, Premium-tier egress
  Singapore to Australia (pages not read).
- Whether **job** labels reach the standard billing export.
- Cloud Run domain mapping in australia-southeast1.
- Cloud Monitoring metric names for Cloud Run jobs.
- The Supabase plan price used in §6.4.

### 7.3 Risks

| Risk | Guard |
|---|---|
| Cold start dominates small batches | Bake the weights into the image, or mount a GCS volume holding the Hugging Face and Kaggle caches (`HF_HOME`, `KAGGLEHUB_CACHE`); measure `t_cold` first |
| Singapore capacity with no zonal redundancy is best-effort | A refused execution fails the `api_jobs` row through the dispatch fallback (§8), never a silent `queued` |
| Cross-region round trips per photo | If step 10 shows it matters, batch the Supabase writes |
| Dev reseeds wipe `media`, `observations`, `api_jobs` | Counts on upload day |
| Long-lived `CLOUD_RUN_TRIGGER_SA_JSON` on the Azure API | One role on one job, deleted at stage 2.2; WIF from the Container App's managed identity is the alternative (CE question 8) |
| Credits not approved by 9 October | Steps 1 to 9 cost nothing; from step 10 a card-backed account for tens of dollars, with Dinnie's explicit approval |
| Two workers on dev | `--max-replicas 0` and the `job_dispatched_cloudrun` log event |
| `gcloud` not installed on the bench machine (checked 2026-09-26) | Install before step 2 |
| The organisation choice is one-way for project ids and IAM | Decide with the CE before the first project |

### 7.4 Questions for the Google customer engineer

1. Which billing account will the accelerator credits land on, in which currency, and do they
   cover Cloud Build, BigQuery, Logging and Premium-tier egress as well as Cloud Run?
2. Any plan for L4 (or any GPU) on Cloud Run in australia-southeast1 or australia-southeast2
   within a year? If not, is asia-southeast1 right over us-central1 for a Sydney database?
3. How often are no-zonal-redundancy GPU job executions refused for capacity in
   asia-southeast1, and can the L4 quota be raised from 3 for a research workload?
4. Is a job per annotate run the shape you would choose, or a GPU service with the idle tail?
   Anything on the roadmap (worker pools scaling to zero, longer GPU task timeouts) that
   changes the answer?
5. Cold start for an 8 to 10 GB CUDA image: does Cloud Run stream image layers, and is a Cloud
   Storage volume for weights faster than baking them in?
6. Do job labels reach the standard billing export, or is the detailed export needed to
   attribute cost per execution?
7. Custom domain for a Cloud Run service in australia-southeast1 behind Cloudflare DNS: domain
   mapping or a load balancer, and at what cost?
8. Workload Identity Federation from an Azure Container App managed identity for the pilot,
   versus a scoped service-account key for three weeks?
9. Is there a Cloud Run metric for "job execution running longer than X", or should the
   stuck-GPU alert be a log-based metric?

## 8. Code changes

The pilot rows are done (the state table in §2); Track 2 rows are not started.

| File | Change |
|---|---|
| `backend/app/config.py` | `CLOUD_RUN_JOB_NAME` (empty = off) and `CLOUD_RUN_TRIGGER_SA_JSON` (inline JSON or path, like `GOOGLE_SERVICE_ACCOUNT_JSON`; empty = Application Default Credentials), beside the `GOOGLE_CLOUD_PROJECT` and `CLOUD_RUN_JOB_REGION` native training added; property `job_offload_configured = bool(REDIS_URL or CLOUD_RUN_JOB_NAME)`. Track 2: `UPLOAD_BUFFER_BACKEND`, `GCS_UPLOAD_BUCKET` |
| `backend/app/jobs/dispatch.py` | New first branch in `enqueue_job` when `CLOUD_RUN_JOB_NAME` is set: `_defer_by` as a delayed start (§4.2), then Admin API `jobs.run` with `container_overrides=[{args: ["-m", "app.jobs.cloudrun_entry", name, json.dumps({"args": args, "kwargs": func_kwargs})]}]`, log `job_dispatched_cloudrun`, return `"cloudrun"`; on any exception, or a payload over 32,000 bytes (a large CamtrapDP import's media ids), fall through to Redis, then local |
| `backend/app/jobs/cloudrun_entry.py` (new) | `python -m app.jobs.cloudrun_entry <job_name> <json>`: resolve the function from `definitions.JOBS`, `asyncio.run` it, `await flush_pending_syncs()`, exit 0 done, 1 failed, 2 bad arguments; `selftest` prints `cuda? True` and logs torch version, CUDA availability, device name and the three model load times |
| `backend/app/jobs/definitions.py` | The upload job's inline and offload branches: `settings.REDIS_URL` becomes `settings.job_offload_configured`, or with `REDIS_URL` unset the upload job runs the AI inline on the lean API image |
| `backend/app/jobs/store.py` | Comment only, at step 12 when the Azure worker is parked: the KEDA window is gone; the reaper is the heartbeat's only consumer |
| `backend/requirements.txt` | `google-cloud-run==0.16.1`, moved from `requirements-ml.txt` so the API image can start executions. Track 2: `google-cloud-storage`; remove `azure-storage-blob`, `aiohttp` at 2.8 |
| `backend/tests/test_cloudrun_worker.py` | `enqueue_job` precedence cloudrun, redis, local; fallback on a refused start and on an oversized payload; `_defer_by` as a delayed start, and its fallback; `cloudrun_entry` running a job, exit codes, argument parsing |
| `backend/cloudbuild.worker.yaml` (new) | §4.1; `gcloud builds submit --tag` would build the last stage (`api`) |
| `.github/workflows/deploy-ml-worker-gcp.yml` (new) | §4.1. Track 2: fold in the API build and retire `deploy-backend.yml`'s Azure steps |
| `scripts/fetch-env.sh`, `fetch-env.ps1` | Track 2: `gcloud secrets versions access latest --secret ww-website-dotenv --project ww-dev`; keep the `--force` guard and byte count |
| `scripts/parity_audit.py` | Env from `gcloud run services describe <svc> --region <r> --format json` (`value`, `valueFrom.secretKeyRef.name`); secret names from those refs plus `gcloud secrets list`; service-role key via `gcloud secrets versions access` (never printed); constants `DEV = ("ww-dev", "australia-southeast1", "ww-backend-dev")`, `PROD`; `--worker` flag; drop `EXPECTED_ENV_DIFFS["REDIS_URL"]` and `pg-conn`; `MUST_DIFFER` gains `GCS_UPLOAD_BUCKET`, loses `AZURE_STORAGE_CONTAINER_NAME`; SQL section unchanged |
| `backend/app/services/object_storage.py` (new, Track 2) | GCS `store_blob`, `retrieve_blob`, `delete_blob`, chosen by `UPLOAD_BUFFER_BACKEND`; imported by `routers/exif.py:38` and `jobs/definitions.py:666` |
| `documentation/` (stage 2.8) | cloud-infrastructure.md becomes the Google Cloud inventory with a `gcloud` checklist; deployment-guide.md, prod-worker-provisioning-runbook.md, onboarding 00 and 01, readme "Secrets & Access", `.env.example`, `docker-compose.yml` comments |

## Open items

None filed yet. On acceptance: one issue per hand-off step (1, 2, 8, 14) and one for the §8 code
PR, linked here.
