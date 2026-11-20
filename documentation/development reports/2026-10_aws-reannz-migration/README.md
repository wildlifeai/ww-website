# AWS and REANNZ migration, approach B

> **Status:** 🔧 Active, 2026-11-20: approach B is built behind settings whose defaults keep today's Azure path. wildlife.ai's AWS organization, the `ww-dev` account and Identity Center sign-in exist ([cloud-infrastructure.md](../../resources/cloud-infrastructure.md#aws)); no stack is deployed. The cluster side runs under the existing REANNZ project `wildlife03546`, where nothing for Wildlife Watcher exists yet. Next: the Lambda quota and the REANNZ email (step 0), then the `ww-dev` stacks (step 1).

The design is [approach-b.md](approach-b.md), the proposal ranked first on 31 October 2026. The estate it replaces is [cloud-infrastructure.md](../../resources/cloud-infrastructure.md); the Google Cloud plan it supersedes is [2026-09_gcp-pilot-and-migration](../2026-09_gcp-pilot-and-migration/README.md).

## What the branch implements

| Slice | What | Switched on by | Main files |
|---|---|---|---|
| 1 Storage seam | One object store over Azure Blob (delegates to `azure_storage.py`), S3 and a local directory; `s3://` originals read and served by a 5-minute redirect; export ZIPs; an S3 archive in place of Drive | `OBJECT_STORE_BACKEND`, `ARCHIVE_BACKEND` | `backend/app/services/object_store.py`, `backend/app/domain/media_resolver.py`, `backend/app/routers/media.py` |
| 2 Batch queue | GPU jobs wait in `api_jobs` with a dispatch record; the reaper spares them and requeues an expired lease once; lease endpoints for the cluster | `WORKER_BACKEND=lambda`, `WORKER_TOKEN_SHA256` | `backend/app/jobs/dispatch.py`, `backend/app/jobs/store.py`, `backend/app/domain/batch_lease.py`, `backend/app/routers/worker.py` |
| 3 Inference split | The cluster runs only the models and writes results parts; ingest runs the existing pipeline steps with precomputed answers behind the three service getters | a lease | `backend/app/jobs/batch_infer.py`, `backend/app/services/inference_cache.py`, `backend/app/jobs/ingest.py` |
| 4 Lambda packaging | SSM bootstrap, `/events` for job invokes and the tick, sync flush before each response, the five in-process call sites through `enqueue_job`, `lambda` and `gpu` image targets, SAM templates, a manual deploy workflow | `WW_LAMBDA_ROLE`, `WW_SSM_PREFIX` (set by the template) | `backend/app/aws_params.py`, `backend/app/lambda_events.py`, `infra/aws/template.yaml`, `infra/aws/shared.yaml`, `.github/workflows/deploy-aws.yml` |
| 5 Uploads through S3 | Presigned POSTs to `incoming/<user>/`; the three upload endpoints take keys or multipart; the browser presigns only when the API says so | `OBJECT_STORE_BACKEND=s3` | `backend/app/routers/uploads.py`, `backend/app/services/incoming.py`, `frontend/src/lib/objectStoreUpload.ts` |
| 6 Cluster and docs | Login-node cron, GPU job, image pull job; a local `batch-infer` service in the compose `gpu` profile; this report | the crontab | `cluster/reannz/ww_cron.sh`, `cluster/reannz/ww_infer.sl`, `cluster/reannz/ww_pull.sl`, `docker-compose.yml` |

## Settings

Defaults are today's behaviour. On AWS the stack sets the first group; every other value, secret or not, is an SSM parameter under `/ww/<env>/`.

| Setting | Default | On AWS | What |
|---|---|---|---|
| `OBJECT_STORE_BACKEND` | `azure` | `s3` | upload buffer, export ZIPs, results parts |
| `ARCHIVE_BACKEND` | `gdrive` | `s3` | where the upload job archives originals |
| `S3_BUCKET`, `S3_REGION` | empty | the stack's bucket and region | |
| `WORKER_BACKEND` | `local` | `lambda` | `local`: ARQ when `REDIS_URL` is set, else in-process. `lambda`: GPU jobs to the batch, others to `CPU_FUNCTION_NAME` |
| `CPU_FUNCTION_NAME` | empty | `ww-<env>-cpu` | |
| `WW_LAMBDA_ROLE` | empty | `api` or `cpu` | mounts `/events`, the flush middleware, skips the lifespan reaper |
| `WW_SSM_PREFIX`, `WW_ENV` | unset | `/ww/<env>/`, `<env>` | read by `app/aws_params.py` before `Settings()` |
| `WORKER_TOKEN_SHA256` | empty (routes answer 404) | SSM | hex SHA-256 of the cluster's lease token |
| `GPU_IMAGE` | empty | SSM, written by the deploy workflow | the tag `/pending` tells the cron to run |
| `S3_PRESIGN_GET_SECONDS` | 300 | | browser and part-upload URLs |
| `BATCH_MIN_PHOTOS`, `BATCH_MAX_WAIT_MINUTES` | 100, 60 | | the submit gate |
| `BATCH_MAX_QUEUE_HOURS` | 24 | | the reaper's limit for a queued batch job |
| `BATCH_LEASE_MINUTES`, `BATCH_MAX_ATTEMPTS` | 30, 2 | | lease expiry without a heartbeat; leases a job may lose |
| `BATCH_LEASE_MAX_PHOTOS`, `BATCH_PART_MAX_PHOTOS` | 2,000, 500 | | lease and part sizes |
| `LOCAL_OBJECT_STORE_DIR` | system temp `ww_objects` | | the `local` backend |
| `SUPABASE_*` | now empty | SSM | empty so the GPU image imports the app; `main.py` refuses to start the API without them |

## Contracts

**Lease routes** (`/api/worker/*`, bearer lease token, not in the OpenAPI document):

| Route | Body | Answer |
|---|---|---|
| `GET /pending` | | `jobs`, `photos` (upper bound), `oldest_minutes`, `submit`, `image`, `time_limit_minutes`, `contract` |
| `POST /lease` | `max_photos`, `slurm_job_id` | the manifest, or `{"lease_id": null}` |
| `POST /lease/{id}/heartbeat` | `done`, `total` | `cancel`, `expires_at` |
| `POST /lease/{id}/part` | `job_id`, `part` | `method`, `url` (S3) or `path` (the API), `headers` |
| `PUT /lease/{id}/part/{job}/{n}` | gzipped part | for the azure and local backends |
| `POST /lease/{id}/part/complete` | `job_id`, `part` | starts `ingest_results_part_job` |
| `POST /lease/{id}/done` | `jobs: [{job_id, parts_total, failed_media}]` | releases the lease, starts `finalize_batch_job` per job |
| `GET /lease/{id}/media/{media_id}?crop=` | | one original or hero crop through the SSRF-guarded resolver |

**Manifest**: `lease_id`, `contract`, `expires_at`, `heartbeat_seconds`, `part_max_photos`, and per job `job_id`, `kind` (`annotate` or `embed`), `models` (`speciesnet`, `bioclip`, or `dinov3` with the model name), `config` (threshold, SpeciesNet cutoffs, `per_detection`, BioCLIP rank and labels), `items` (`media_id`, `deployment_id`, and `url` for S3 or a public rendition, else `path`).

**Results part** (`backend/app/services/inference_cache.py`, contract 1): gzipped JSON lines. Header: `contract`, `lease_id`, `job_id`, `part`, `kind`, `image`, `slurm_job_id`, `bioclip_labels`, `bioclip_rank`, `embedding_model`. Record per photo: `media_id`, `deployment_id`, then `speciesnet` (the `ImagePrediction` fields), `bioclip` (`bbox` or null for a whole frame, name, score, rank), `dinov3` (the vector), or `error`.

**Keys**: `originals/{project}/{deployment}/{file_hash}.jpg`, `incoming/{user}/{upload}/{n}-{name}`, `buffer/...`, `exports/{job}.zip`, `results/{lease}/{job}/part-NNNN.jsonl.gz` with `.ingested`, `_done.json` and `_finalized` markers. Lifecycle in `infra/aws/template.yaml`.

**`api_jobs.job_data`** (jsonb, no schema change): `dispatch` (`backend: batch`, `name`, `kind`, `args`, `kwargs`, `queued_at`, `not_before`, `attempts`) and, once leased, `lease` (`id`, `leased_at`, `slurm_job_id`). The lease itself is a `services/locks.py` lease row named `batch-lease:<id>`.

**Lambda events** (`POST /events` through the Web Adapter): `{"ww-job": name, "args", "kwargs"}` or with `payload_key` over 200 KB, on the `cpu` function only; `{"ww-tick": true}` from EventBridge Scheduler: reaper plus one Embedded Metric Format line (`WildlifeWatcher/BatchOldestQueuedMinutes`).

## Where the build departs from approach-b.md

| approach-b.md | Built | Why |
|---|---|---|
| A results part's S3 ObjectCreated event invokes ingest | The cluster's `POST /part/complete` starts ingest | Works on every backend, so the batch can be tried with the API still on Azure; no bucket notification; a part of an expired lease is not ingested by surprise |
| Three functions, a separate ingest function | Two, `api` and `cpu`; ingest is a CPU job | The `cpu` function's reserved concurrency (5) is the Supabase guard either way |
| DINOv3 vectors in the annotate pass, matched to the hero crop by box | A follow-up `embed_deployments_job` batch after the annotate job finishes | The hero crop exists only after ingest writes it; the embed lease ships those exact bytes. Costs one more lease, in the same or the next window |
| Five lease endpoints | Eight routes (above) | A part PUT for non-S3 backends and a media route, so `gdrive://` originals reach the cluster without a raw URL or a credential |
| Lease state in `job_data` | A lease row in `services/locks.py` plus marker objects | A process with a stale memory copy of a job (the job store is memory-first) could overwrite `job_data` fields; rows and markers it never writes |
| `results/{env}/...` | `results/{lease}/{job}/...` | One bucket per environment already |
| `originals/{org}/{project}/{deployment}/{sha256}` | `originals/{project}/{deployment}/{file_hash}.jpg` | The upload payload carries no organisation; `file_hash` is `google_drive.compute_file_hash` (bytes and deployment id), the existing dedup key |
| Incremental embedding | Not built | Not needed for correctness: an embed lease sends all the deployment's crops, as `embed_and_cluster_deployment` re-embeds them today. Ingest no longer downloads the crops |
| HMAC export link | Presigned GET with the existing 24-hour lifetime | See "Left": on Lambda it dies with the role's session |
| Training as a tick-driven state machine | Not built | See "Left" |
| Accounts not specified | One AWS account per environment (`ww-dev`, later `ww-prod`) in wildlife.ai's organization; `ww-shared` deploys in each, its role trusting only that account's GitHub environment (`GitHubEnvironment`) | A dev mistake cannot reach prod, and the management account holds no workloads |
| Compose `gpu` profile replaces Redis and the ARQ worker | `batch-infer` added to the `gpu` profile, inert without `WW_WORKER_TOKEN` | The Azure path stays until teardown |

On the hero-crop BioCLIP path (`FF_PER_CROP_CLASSIFY_ENABLED` off) a Gemini box that outranks every SpeciesNet box gives a hero the cluster did not crop; that photo gets no BioCLIP row and the part counts a miss. The per-crop path, on in production, matches exactly (parity tests).

## Left, not in this branch

| Item | Effect until done | Where |
|---|---|---|
| Export link that outlives the Lambda role session (HMAC route redirecting to a fresh presigned URL) | An emailed export link can stop working within hours on AWS | `backend/app/domain/camtrapdp_export.py`, `backend/app/routers/exports.py` |
| `train_species_brain_job` resumable, polled by the tick | Can exceed the 15-minute limit on the `cpu` function; keep `FF_MODEL_TRAINING_ENABLED` off on AWS | `backend/app/jobs/definitions.py` |
| `reprocess_project_job` and `reprocess_all_job` fanned out to per-deployment embed batches | On AWS they reach the `cpu` function, which cannot embed, and fail with a clear message | `backend/app/jobs/dispatch.py` |
| Drive archive copy and path rewrite (`migrate_archive_job`, step 7) | `gdrive://` rows keep resolving through the media route | |
| Upload dock copy: cloud AI arrives in the next batch window | Users see a queued AI job for up to the window | `frontend/src/contexts/UploadContext.tsx` |
| `scripts/parity_audit.py`, `scripts/fetch-env.sh` for SSM and Lambda | Azure-only until then | |
| A test of the flush middleware on a Lambda-mounted app | Covered by reading only | `backend/app/main.py` |
| Least-privilege review of `infra/aws/shared.yaml`'s deploy role; action versions in `deploy-aws.yml` | Neither has run | |
| Executable bit on `cluster/reannz/*` | Set with `git update-index --chmod=+x` when committing, or `chmod +x` on the cluster | |

## ww-backend, issue descriptions for Victor (none required by this branch)

1. **Record where originals live.** `media_assets.storage_provider` allows `google_drive`, `azure_blob`, `supabase_storage`. Add `aws_s3` only if the column should be filled; the website leaves it NULL today (`backend/app/domain/media_registry.py`).
2. **Comments.** `media.file_path` ("Storage path or Google Drive path") and the `media-renditions` comment in `supabase/schemas/storage/01_buckets.sql` ("Originals stay in Google Drive") should name S3.
3. **The batch queue's reads.** `api_jobs` is filtered on `job_data->dispatch->>backend` and `job_data->lease->>id`. Small today; an expression index when it grows, or the `ai_work_items` table from approach C, which the review recommends once batches are proven.

## Migration steps

From [approach-b.md](approach-b.md), section 4, in approach A's order as the review recommends.

| # | Step | Status | Rollback |
|---|---|---|---|
| 0 | REANNZ email (login-node crontab, compute-node HTTPS to the API host, memory above the L4 bundle, a service account for the job); Supabase region; AWS account, Auckland enabled, Lambda quota | Wildlife Watcher runs under the existing project `wildlife03546`, beside Spyfish, not a new one (decided 2026-11-20). AWS done 2026-11-19; the Lambda quota (`ww-dev` allows 10 concurrent executions, the stack reserves 15 and AWS keeps 100 unreserved) has 1,000 requested 2026-11-20 (Service Quotas, `L-B99A9384`), pending. REANNZ email drafted, Supabase region not read | none |
| 1 | `infra/aws/shared.yaml` (`GitHubEnvironment=dev`), then `ww-dev` from `infra/aws/template.yaml` with bucket `wildlifewatcher-dev-media`, in the `ww-dev` account; SSM parameters | not started; templates written, never deployed | delete the stacks |
| 2 | Code behind flags into `dev` | on `feat/aws-reannz-batch`, [pull request #442](https://github.com/wildlifeai/ww-website/pull/442) into `dev` (2026-11-20), not merged | revert |
| 3 | Dev API on Lambda; Pages preview `VITE_API_BASE_URL`; `ALLOWED_ORIGINS` | not started | Pages preview env back to the ACA FQDN |
| 4 | Dev cluster: SIF, weights, token, crontab; two weeks measuring seconds per photo, cold start, queue wait, RSU, ingest time, precomputed misses | not started; scripts written | comment out the crontab line |
| 5 | `ww-prod`; the Azure prod API gets the `s3://` read path (in this branch) and a read-only key | not started | delete the stack |
| 6 | Prod cutover | not started | Pages env back |
| 7 | Drive archive copy | not built | reverse run |
| 8 | Teardown of `WW-AE` and the Azure, Redis, ARQ, KEDA and Drive code | not started | none |

## Setting up step 1 and step 4 by hand

| What | Where |
|---|---|
| Lease token: `python -c "import secrets; print(secrets.token_urlsafe(32))"`; its SHA-256 to `/ww/<env>/WORKER_TOKEN_SHA256` | the raw token only in `~/.config/ww/<env>.token` on the cluster, mode 600, written by Victor |
| API URL for the cron | `~/.config/ww/<env>.api`, the stack's `ApiUrl` output |
| Every other setting and secret | SSM SecureString `/ww/<env>/<NAME>` |
| Signing in to AWS | Identity Center, the `ww-dev` profile: [cloud-infrastructure.md](../../resources/cloud-infrastructure.md#signing-in-to-aws) |
| Variables for `deploy-aws.yml` | Per GitHub environment (`dev`, `staging`), because each is its own AWS account: `AWS_DEPLOY_ROLE_ARN`, `ECR_REGISTRY`, `AWS_REGION` (`ap-southeast-6`). Repository-wide: `AWS_DEPLOY_ENABLED`, `AWS_BUCKET_DEV` (`wildlifewatcher-dev-media`), `AWS_BUCKET_PROD` (`wildlifewatcher-prod-media`), `AWS_ALARM_EMAIL` |
| The first `ww-dev` deploy | `workflow_dispatch` only runs a workflow that exists on the default branch (`main`), so until a release carries `deploy-aws.yml` there, step 1 runs from a workstation: Docker with buildx for the arm64 `lambda` image, the SAM CLI, and the `ww-dev` profile |
| GHCR package visibility | public, once, after the first push of `ww-backend-gpu` |
| Cluster layout | `/nesi/project/wildlife03546/ww/{bin,sif,models,slurm_logs,logs}`; the three scripts in `bin/`; weights under `models/` (`hf`, `kaggle`, `torch`) cached once with `HF_TOKEN` in the session only |
| Crontab | `*/30 * * * * /nesi/project/wildlife03546/ww/bin/ww_cron.sh <env> >> /nesi/project/wildlife03546/ww/logs/cron-<env>.log 2>&1` |
