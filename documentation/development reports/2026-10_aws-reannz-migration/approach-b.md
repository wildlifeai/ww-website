# Approach B: serverless and event-driven, lowest fixed cost

> **Status:** 📋 Proposal, frozen as ranked first on 31 October 2026. Where the build departs from it, and what is built: [README.md](README.md).

31 October 2026. A design, not a decision. Read against ww-website `dev` at cd2c6ed, ww-backend `supabase/schemas`, the Spyfish toolkit at f2ec949 and the AWS and REANNZ research report of 29 October 2026 (kept outside this repository). AWS prices come from the price list API for Auckland (`ap-southeast-6`), downloaded 31 October 2026 UTC; REANNZ prices from the report. NZD at 1 USD = 1.7634 NZD. Figures marked *memory* are unverified; volumes marked *assumed* are my estimates, not prices.

## 1. Summary

1. The API, the CPU jobs and a results-ingest step run as three Lambda functions from one arm64 image; originals live in one private S3 bucket per environment, written by the browser through presigned POSTs. Supabase and Cloudflare stay. Azure, Redis, ARQ, KEDA and the Google Drive archive retire.
2. GPU trigger: a crontab on the REANNZ login node asks the API every 30 minutes whether cloud AI work is queued and, if so, submits one singleton L4 Slurm job. The job leases a manifest of short-lived photo URLs over HTTPS, runs SpeciesNet, BioCLIP and DINOv3 and nothing else, and PUTs one results file per part to S3.
3. Results return as data, not database writes: the file's ObjectCreated event invokes the ingest Lambda, which runs the existing `run_pipeline` code with the inference already done. The cluster holds one revocable lease token, no database credential and no AWS key.
4. Nothing on AWS is always on, so the fixed floor is a few dollars a month (section 5).
5. The trade-off accepted: it is the most code of any shape. The pipeline is split at its four model calls, and every upload is re-plumbed through S3 because Lambda refuses request bodies over its payload limit.

## 2. Architecture

| Component | Region / host | Job | Replaces |
| --- | --- | --- | --- |
| `ww-<env>-api` Lambda, Function URL, arm64, 1,024 MB, Lambda Web Adapter (LWA), response streaming | AWS, one region (below) | FastAPI as today; presigns uploads; serves the lease endpoints | ACA `ww-backend`, `ww-backend-dev` |
| `ww-<env>-cpu` Lambda, 3,008 MB, 900 s, 10 GB `/tmp`, invoked asynchronously | same | upload registration, model conversion, manifests, CamtrapDP exports, Drive migration, training state machine | `enqueue_local_job` on the API, the CPU half of the ARQ worker |
| `ww-<env>-ingest` Lambda, 3,008 MB, 900 s, reserved concurrency 2 | same | one results part: media prep, observations, crops, Gemini, fusion, then incremental embedding, clustering, notifications | the database half of the ARQ worker |
| `ww-<env>-tick` EventBridge Scheduler, every 5 min prod, 15 min dev | same | stale-job reaper, oldest-queued metric, training poll, keeps one API environment warm | the lifespan reaper loop, prod `min-replicas 1` |
| S3 `wildlifewatcher-<env>-media`, Block Public Access, SSE-S3 | same | the only store of originals; incoming, buffer, exports, results | Blob `wwuploadsae`, Google Drive `Data/` |
| SQS `ww-<env>-dlq` (not polled, alarmed) | same | on-failure destination of every async invoke | ARQ `max_tries = 1` silence |
| SSM Parameter Store `/ww/<env>/*` SecureString | same | runtime secrets, read once per cold start | ACA secrets, Key Vault `ww-kv-dev-ae` |
| ECR `ww-backend` | same | the Lambda image | ACR `wwregistry` |
| GHCR `ghcr.io/wildlifeai/ww-backend-gpu` (public, no weights, no secrets) | GitHub | the GPU image, pulled by Apptainer | ACR `ww-backend-worker` |
| `ww_cron.sh`, `ww_infer.sl` | REANNZ login node, `genoa` L4 | pull work, run inference | KEDA Postgres scaler, the T4 profile |

**Region.** Everything in one region, because inter-region transfer between Auckland and Sydney costs on every ingest read (section 5). Auckland is the default, for the GPS in the EXIF and a domestic path to the cluster. If Supabase turns out to be in Sydney, each PostgREST call from the API gains the trans-Tasman round trip; the guard is in section 7.

**Bucket layout.**

```
originals/{organisation_id}/{project_id}/{deployment_id}/{sha256}.{ext}  Standard, Glacier Instant Retrieval after 90 days
incoming/{user_id}/{upload_id}/{n}-{name}                                expires after 1 day
buffer/{job_id}/...                                                      expires after 1 day (conversion, manifests, large payloads)
exports/{job_id}/{name}.zip                                              expires after 7 days
results/{env}/{lease_id}/part-{nnnn}.jsonl.gz                            expires after 90 days; ObjectCreated invokes ingest
```

Originals are keyed by the SHA-256 the upload already computes for deduplication (`media.file_hash`), so a repeat upload lands on the same key and camera filenames, which can repeat across SD cards, never collide. `media.file_name` keeps the camera's name.

**Upload to cloud AI result.**

1. `apiClient.upload()` asks `POST /api/uploads/presign`, POSTs each file straight to `incoming/`, then calls `/api/exif/parse` with the keys instead of the bytes.
2. The API reads the objects from S3 and runs the existing parse, access and test-photo checks. It copies accepted files to `originals/` and async-invokes `ww-<env>-cpu` with the upload job.
3. The upload job registers `media` rows with `s3://` paths, reflects the camera's own scores (the user sees Camera AI at once), and reserves or joins the `ai_pipeline` job exactly as today (#284). With `WORKER_BACKEND=lambda`, `dispatch.enqueue_job` writes the GPU job's name and arguments into `api_jobs.job_data.dispatch` and leaves the row `queued`. No broker exists.
4. The cron calls `GET /api/worker/pending`. The API answers `submit` from its own gate (table below), plus the GPU image tag and a `--time` sized to the queue.
5. The job starts, calls `POST /api/worker/lease`, and receives presigned GETs for the originals, public rendition URLs for crops that need vectors, and per-deployment model settings. The API marks the leased jobs `processing` with a lease expiry. Every 5 minutes the job calls `.../heartbeat`, which refreshes `api_jobs.updated_at`.
6. The job downloads to nobackup, loads one model at a time, and writes per photo the SpeciesNet prediction plus, for every detection the cutoffs keep, BioCLIP labels and, for the hero detection, the DINOv3 vector. Each part of up to 500 photos goes up through a PUT URL fetched at that moment. The job leases again until the API returns nothing, then exits.
7. Ingest validates the part against its lease and the `inference_contract` version, then runs `run_pipeline(media_ids=...)` with precomputed providers behind `get_speciesnet_service()`, `get_bioclip_service()` and `get_dinov3_service()`. The last part of a job triggers incremental embedding, clustering, notifications and `completed`.

| Lease setting | Default | Why |
| --- | --- | --- |
| Gate: submit when queued photos reach | 100, or the oldest queued job is 60 min old | bounds cold starts (section 5); server-side, so tuning needs no cluster login |
| Photos per lease | 2,000 | keeps the manifest small |
| Photos per results part | 500 | keeps each ingest well inside 15 minutes |
| Lease expiry without heartbeat | 30 min; requeued once, then failed | a lost Slurm job never strands a row |
| Queued batch jobs spared by the reaper | 24 h (`BATCH_MAX_QUEUE_HOURS`) | today's 60-minute reaper would fail every job waiting for a window |

**Slurm.** The cron skips submission while `squeue -n ww-<env>-infer` shows a job, and pulls a new SIF in a small CPU job when the tag changes. The job script carries the Spyfish lessons:

```bash
#!/bin/bash -e
# One job per environment at a time (Spyfish: never two jobs on the same survey).
#SBATCH --job-name=ww-prod-infer
#SBATCH --dependency=singleton
#SBATCH --account=wildlife03546
# L4 in genoa; the Blackwell pro_6000 failed Spyfish's torch (run_pipeline.sl).
#SBATCH --partition=genoa
#SBATCH --gpus-per-node=l4:1
# DINOv3 ViT-H was OOM-killed at 8 Gi on ACA. The cron passes --time sized to the queue.
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --output=/nesi/project/wildlife03546/ww/slurm_logs/%x_%j.out
# module use: the milan module-tree trap in run_pipeline.sl and train_job.sl.
module purge; module use /opt/nesi/lmod/mahuika; module load Apptainer
# SIF and WW_API arrive from the cron through sbatch --export. nobackup is staging only.
WORK=/nesi/nobackup/wildlife03546/ww/$SLURM_JOB_ID; mkdir -p "$WORK"; trap 'rm -rf "$WORK"' EXIT
run() { apptainer exec --nv --bind "$WORK":/work,/nesi/project/wildlife03546/ww/models:/models "$SIF" "$@"; }
# Fail fast without a GPU (run_pipeline.sl).
run python -c "import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)"
run python -m app.jobs.batch_infer --api "$WW_API" --token-file "$HOME/.config/ww/prod.token" --work /work
```

`HF_HUB_OFFLINE=1` and `KAGGLEHUB_CACHE=/models/kaggle` point at weights Victor caches once by hand. The job uses `curl` and Python only, so the `aws`-only-in-login-shells trap (run_pipeline.sl, run 8598080) cannot bite. Every cron log line is timestamped.

**Secrets.**

| Secret | Lives in | Read by |
| --- | --- | --- |
| Supabase URL, anon and service-role keys, Gemini, Resend, iNat, demo, Sentry | SSM `/ww/<env>/` | the three Lambdas only |
| Lease token (random, one per environment) | raw in `~/.config/ww/<env>.token`, mode 600, home not project directory; SHA-256 in SSM | the cron and the job; written by Victor in his own session, never by an agent |
| `HF_TOKEN`, Kaggle | nowhere persistent; used once by Victor to fill `/nesi/project/.../ww/models` | nobody at run time |
| AWS deploy rights | GitHub OIDC role `ww-gha-deploy` | CI |

A leaked lease token can read the photos of queued jobs for a short window and submit results that ingest checks against the lease. Rotation is one SSM write and one file; nobody is logged out.

**Redis, KEDA, ARQ.** All three go. Locks already fall back to `api_jobs` lease rows (`services/locks.py`, #323). The job store already hydrates across processes from Supabase. Nothing runs ARQ's queue, so `GPU_PENDING_KEY` and the dual-queue drift that `gpu-worker-infra-spec.md` §12(a) calls a production-failure surface disappear with it. The stuck-GPU guard becomes structural: a job exits when its lease comes back empty, Slurm `--time` is the hard stop, and `singleton` forbids a second job.

**No CloudFront, no API Gateway.** The frontend already calls the API on its provider hostname (the ACA FQDN today), so the Function URL needs no DNS change, and the renditions already sit on Supabase's CDN. Either service would add a bill line without removing a risk. Cloudflare stays for Pages and DNS.

**Where scale-to-zero hurts, and the choice made.**

| Cost of scale-to-zero | Choice |
| --- | --- |
| "Try the demo" after idle pays a Lambda cold start, where ACA paid about a minute | No always-on piece at first. The tick keeps one environment warm; if the measured init is too slow, provisioned concurrency on the API function only (priced in section 5). The demo's data stays precomputed, so it never waits on a batch |
| The 15-minute Lambda limit | Nothing GPU and no long poll runs on Lambda: parts of 500 photos, training as a state machine, exports keep their size cap |
| Supabase connection churn | Does not apply: the code reaches Supabase over PostgREST HTTPS, and the only pooled Postgres client, the KEDA scaler, is deleted |
| A frozen environment drops work started after the response | Every `enqueue_local_job` becomes an async invoke, and a middleware flushes detached job-status syncs before responding |
| In-memory rate limits per environment | Accepted; reserved concurrency is the ceiling |

**Why the GPU queue is `api_jobs`, not SQS or S3 events.** The cluster cannot be pushed to, and polling SQS from it would need an AWS key there. An original's ObjectCreated event fires before its `media` row exists and before coalescing, so it is the wrong unit of work. A second queue beside `api_jobs` would recreate the drift above. S3 events are used where the object is the work: results parts. Async invokes replace SQS for CPU jobs, because a polled queue bills its pollers' requests even when idle (*memory*).

| Where B departs from the report | Why |
| --- | --- |
| No dedicated Postgres role through the session pooler | The worker never speaks Postgres: every call is supabase-py over PostgREST with the service-role key, and `app/` has no psycopg or asyncpg. A pooler role means rewriting data access; B removes the need for a credential instead |
| The cron asks the API, not `api_jobs` | A database query from the login node needs a database credential there |
| Writes happen in ingest, not in the job | The L4 clock pays for inference only; media prep and row writes move to Lambda |
| The S3 seam is not `blob_store.py` alone | `blob_store.py` is the local-disk store for conversion; the upload buffer and export ZIPs are `azure_storage.py` (`exif.py:38`, `definitions.py:1088`, `camtrapdp_export.py:385`). One `object_store.py` replaces both |
| `s3://` already exists | `media_resolver.py` registers a `_resolve_s3` placeholder; only its body is missing |
| The reaper needs a change | The report's batch window would trip the 60-minute stale-job reaper |
| One region | S3 in Auckland with the API elsewhere bills every ingest read |

## 3. Code changes by repo

**ww-website, backend** (paths under `backend/`).

| File | Change |
| --- | --- |
| `app/services/object_store.py` (new) | S3 and local backends: presigned POST and GET, get, multipart put, copy, delete; `store_blob`/`retrieve_blob`/`delete_blob` over `buffer/` |
| `app/routers/uploads.py` (new) | `POST /api/uploads/presign`, size capped per purpose |
| `app/services/incoming.py` (new) | dependency yielding files from multipart parts or `*_key` fields; used by `exif.py`, `camtrapdp.py`, `models.py` |
| `app/routers/worker.py` (new) | `pending`, `lease`, `lease/{id}/heartbeat`, `lease/{id}/part`, `lease/{id}/done`; bearer token compared with the SSM hash; `include_in_schema=False` |
| `app/domain/batch_lease.py` (new) | FIFO claim under `exclusive("batch-lease")`, manifest, expiry, requeue |
| `app/jobs/batch_infer.py` (new) | the cluster entry point; no database, no flags, runs what the manifest lists |
| `app/services/inference_cache.py` (new) | loads a part; precomputed providers keyed by `(media_id, bbox)` |
| `app/jobs/ingest.py` (new) | S3 event handler; idempotent per `(lease, part)`; finalises the job |
| `app/lambda_events.py` (new) | `/events` router for LWA pass-through, mounted only when `WW_LAMBDA_ROLE` is `cpu` or `ingest`, plus a harmless `tick` on the API |
| `app/aws_params.py` (new) | loads `/ww/<env>/` into the environment before `Settings()` |
| `app/config.py` | `WORKER_BACKEND=local\|lambda`, `OBJECT_STORE_BACKEND=local\|s3`, `S3_BUCKET`, `CPU_FUNCTION_NAME`, `WORKER_TOKEN_SHA256`, `BATCH_*`; Supabase fields default empty so the GPU image starts without them, and the API refuses to start without them |
| `app/jobs/dispatch.py` | route by job name: GPU names to a `queued` row with `job_data.dispatch`, CPU names to `lambda:Invoke` (Event, 0 retries, DLQ); local path unchanged; Redis branch and `GPU_PENDING_KEY` deleted |
| `app/jobs/definitions.py` | upload job writes `s3://` and drops the Blob and Drive phases; the `settings.REDIS_URL` conditions at lines 1520 and 1600 test `WORKER_BACKEND`, or with Redis gone the upload job would run the AI inline; batch path for annotate, embed and reprocess jobs; training becomes a start, poll, finish state machine driven by the tick; export writes `exports/`; new `migrate_archive_job` |
| `app/jobs/store.py` | reaper spares queued batch jobs, requeues expired leases once |
| `app/domain/pipeline.py` | `hero_detection()` extracted and shared with `batch_infer`; `classify()` gains `keys` at its two call sites; media selection extracted for the lease |
| `app/domain/wildlife_brain.py` | embed only crops with no vector for the active `embedding_model`, cluster over stored plus new vectors |
| `app/services/{speciesnet_service,bioclip_service,dinov3}.py` | getters return the precomputed provider while a part is active; `keys` parameter |
| `app/domain/media_resolver.py` | `_resolve_s3` body |
| `app/routers/media.py` | `/image` on an `s3://` row redirects to a 5-minute presigned GET instead of proxying bytes |
| `app/routers/exif.py`, `manifest.py`, `models.py` | `enqueue_local_job` (exif.py:636, manifest.py:39, models.py:153, 323, 336) becomes `enqueue_job` |
| `app/domain/camtrapdp_export.py`, `app/routers/exports.py` | the emailed link is `GET /api/exports/{job_id}/download?sig=` (HMAC, 24 h) redirecting to a fresh presigned URL, because a URL signed with Lambda role credentials dies with the session |
| `app/main.py` | no reaper loop under Lambda; middleware awaits `flush_pending_syncs()` before each response, because a frozen environment drops detached syncs |
| `Dockerfile` | `lambda` target: base, LWA extension, `requirements-cpu-ml.txt` (numpy, hdbscan, umap-learn); `worker` renamed `gpu`, CMD `python -m app.jobs.batch_infer` |
| `requirements.txt` | add `boto3`; remove `arq`, `redis`, `azure-storage-blob`, `aiohttp` and the Drive client at teardown |
| `docker-compose.yml` | drop `redis` and `embedding-worker`; a `gpu` profile runs `batch_infer` against the local API |
| `tests/` | lease, expiry and requeue; ingest of one part twice; a parity test (fixture photos through real models versus through the cache give identical rows); dispatch routing; reaper windows; presign caps |

**ww-website, frontend, cluster, scripts, docs.**

| File | Change |
| --- | --- |
| `frontend/src/lib/apiClient.ts` | `upload()` presigns, POSTs each File to S3 and sends keys; `UploadContext.tsx:417`, `UploadFlow.tsx:313`, `UploadModel.tsx:164` unchanged |
| upload dock copy | cloud AI "arrives in the next batch window" |
| `cluster/reannz/ww_cron.sh`, `ww_infer.sl`, `ww_pull.sl` (new) | the cron, the job, the SIF pull |
| `scripts/fetch-env.sh`, `fetch-env.ps1`, `parity_audit.py` | SSM and `aws lambda get-function-configuration` instead of Key Vault and `az` |
| `documentation/resources/cloud-infrastructure.md`, `deployment-guide.md`, a REANNZ runbook, `AGENTS.md` | AWS and REANNZ inventory; the "set AI flags on the ARQ worker" rule goes, flags live once in the stack |

**ww-backend: nothing required.** The cluster never connects to the database and `api_jobs.job_data` is jsonb. Two optional issues for Victor:

1. *Record where originals live.* `media_assets.storage_provider` allows `google_drive`, `azure_blob`, `supabase_storage`; add `aws_s3` only if Victor wants the column filled. The website leaves it NULL today (`media_registry.py:141`).
2. *Comments.* `media.file_path` says "Storage path or Google Drive path" and `01_buckets.sql` says "Originals stay in Google Drive"; both should name S3.

**Infrastructure and CI.**

| File | Change |
| --- | --- |
| `infra/aws/template.yaml` (new, SAM, one stack per environment) | bucket with CORS for the Pages origins, lifecycle rules and the `results/` notification; three functions; Function URL; scheduler; DLQ; alarms (DLQ depth, errors per function, oldest queued over 6 h); least-privilege roles; budget |
| `.github/workflows/deploy-backend.yml` | OIDC; `--target lambda` arm64 to ECR and `sam deploy` (dev on `dev`, prod on `main`, as today); `--target gpu` amd64 to GHCR; tag written to `/ww/<env>/GPU_IMAGE` so the cron follows deploys |
| `ci.yml` | the Docker check builds the `lambda` target |
| ECR lifecycle policy | keep 5 sha tags and `stable`; replaces the ACR `weekly-purge` task |

**Deleted.** `jobs/worker.py`, `services/blob_store.py`, `services/azure_storage.py`, `services/google_drive.py` (after the archive copy), the Redis branches in `locks.py` and `google_drive.py`, the Azure deploy steps, ACR and its purge task, both KEDA rules, the Azure Monitor alerts, Key Vault, `pg-conn`, `google-sa-json`, `WW-AE`.

## 4. Migration steps

| # | Step | Rollback | Weeks from start |
| --- | --- | --- | --- |
| 0 | Email engagement@reannz.co.nz (allocation class of `wildlife03546`, may Wildlife Watcher run under it, login-node crontab, Apptainer, compute-node HTTPS to a public host); read the Supabase region; open the AWS account, enable Auckland, raise the Lambda concurrency quota | none | 0 to 3, waiting in parallel |
| 1 | Deploy `ww-dev` from `infra/aws/template.yaml` | delete the stack | 1 |
| 2 | Code PRs into `dev` behind `WORKER_BACKEND=local` and `OBJECT_STORE_BACKEND=local`, so Azure runs unchanged | revert | 2 to 6 |
| 3 | Dev cutover: dev API on Lambda, Pages preview `VITE_API_BASE_URL` to the Function URL, `ALLOWED_ORIGINS`; dev AI queues until step 4 (dev reseeds anyway) | Pages preview env back to the ACA FQDN, redeploy | 6 |
| 4 | Dev cluster: SIF, weights, token, crontab. Two weeks measuring seconds per photo, cold start, queue wait, RSU used (`sacct`), ingest time, precomputed misses, API latency | comment out the crontab line | 6 to 8 |
| 5 | `ww-prod` stack; parity audit; the Azure prod API gets the `s3://` resolver and a read-only key so a rollback can still serve new rows | delete the stack | 8 |
| 6 | Prod cutover: deploy freeze; let the Azure prod queue drain; Pages production env to the prod Function URL; prod crontab line; check `/health`, Try the demo, one upload with cloud AI | Pages env back and redeploy; AI jobs created on AWS are reaped and re-run | 9 |
| 7 | `migrate_archive_job` copies Drive originals in chunks and rewrites paths, logging old paths to `migrations/drive-<date>.jsonl`; Drive files kept until Victor signs off. A data change, so it goes to Victor first | the reverse run restores `gdrive://` paths | 9 to 10 |
| 8 | Two quiet weeks, then delete `WW-AE`, the read-only key, the Azure, Redis, ARQ, KEDA and Drive code and settings; rewrite the docs | none, the point of no return | 12 |

About 12 weeks elapsed, of which about 6 are engineering (*assumed*).

## 5. Ongoing cost

| Line | USD | NZD | Source |
| --- | --- | --- | --- |
| Fixed monthly floor, both environments | 2.11 | 3.72 | breakdown below |
| Per 1,000 photos, AWS | 0.165 | 0.291 | breakdown below |
| Per 1,000 photos, L4 at $0.41 per hour, 1.5 s each | 0.097 | 0.171 | REANNZ slide (report), NZ$ assumed there |
| Per 1,000 photos, L4 under a research allocation | 0 | 0 | report |
| Per Slurm job, 120 s cold start | 0.0078 | 0.0137 | REANNZ slide |
| Per TB-month, S3 Standard | 26.88 | 47.40 | price list, APS8 Standard, first 50 TB |
| Per TB-month, Glacier Instant Retrieval | 5.38 | 9.48 | price list, APS8 GIR |
| Per TB read back from GIR (retrieval plus GETs at 0.5 MB) | 51.20 | 90.29 | price list, APS8 GIR retrieval and GET |
| Per GB moved between Auckland and Sydney, either way (why B keeps one region; not in the totals) | 0.08 | 0.14 | price list, AWSDataTransfer APS8-APS2 and APS2-APS8 |
| **Small, list** | **9.05** | **15.95** | scenario table |
| **Small, research allocation** | **7.13** | **12.58** | scenario table |
| **Large, list** | **51.51** | **90.84** | scenario table |
| **Large, research allocation** | **39.00** | **68.77** | scenario table |

All lines are list prices. The always-free allowances (Lambda 400,000 GB-s and 1 M requests, 100 GB internet egress, 5 GB logs, 10 alarms, from the same price files) would zero most AWS lines in both scenarios. Whether they apply once per AWS Organization is *memory*.

| Fixed floor item | USD a month | Source |
| --- | --- | --- |
| Lambda idle | 0 | price list: per request and GB-s only |
| Tick, 11,520 invocations at 0.1 GB-s | 0.02 | price list, APS8 ARM GB-s and requests; Scheduler free tier *memory* |
| ECR, 4 GB | 0.40 | price list, ECR APS8 US$0.10 per GB-month |
| CloudWatch Logs, 1 GB baseline | 0.53 | price list, APS8 ingest US$0.50 per GB, storage US$0.03 |
| Six alarms | 0.60 | price list, APS8 US$0.10 per alarm |
| One custom metric (oldest queued minutes) | 0.30 | price list, APS8 US$0.30 per metric |
| Dev bucket, 10 GB Standard | 0.26 | price list |
| SSM standard parameters, DLQ at rest, GHCR public, Cloudflare | 0 | *memory* for SSM; others free plans |
| **Total** | **2.11** | |
| Optional: provisioned concurrency 1 x 1 GB for the demo | 11.57 | price list, APS8 ARM provisioned concurrency |

| Per 1,000 photos, AWS | USD | Basis |
| --- | --- | --- |
| S3 requests: 2 PUT or COPY, 4 GET, 1 transition to GIR | 0.0334 | price list, APS8 request tiers |
| Lambda: 1,800 GB-s ARM (parse 0.2, register 0.1, ingest 1.5 per photo, *assumed*) | 0.0252 | price list, APS8 ARM US$0.0000140001 per GB-s |
| Egress, S3 to the cluster, 0.5 GB | 0.0570 | price list, APS8 internet US$0.114 per GB |
| Egress, Lambda to Supabase, 0.2 GB (*assumed*) | 0.0228 | same rate |
| Logs, 0.05 GB (*assumed*) | 0.0265 | price list, CloudWatch APS8 |
| **Total** | **0.165** | |

| Scenario | Small | Large | Basis |
| --- | --- | --- | --- |
| Originals in Standard (last 90 days) | 15 GB | 150 GB | 0.5 MB a photo |
| Originals in GIR | 497 GB | 2,922 GB | the rest |
| Storage | US$3.00 | US$19.28 | price list |
| Storage if all Standard | US$13.44 | US$80.64 | for comparison with approaches that keep Standard |
| AWS per photo | US$1.65 | US$16.49 | per-1,000 table |
| API traffic (100,000 and 300,000 requests, 0.25 GB-s each, *assumed*) | US$0.37 | US$1.11 | price list |
| Fixed floor | US$2.11 | US$2.11 | floor table |
| L4 inference (4.17 and 41.67 L4-hours) | NZ$1.71 | NZ$17.08 | REANNZ slide |
| Jobs per day priced | 4 | 12 | gate; bursty uploads |
| Cold starts (4.06 and 12.18 L4-hours) | NZ$1.66 | NZ$4.99 | REANNZ slide |
| **Total, list** | **US$9.05 (NZ$15.95)** | **US$51.51 (NZ$90.84)** | |
| **Total, allocation** | **US$7.13 (NZ$12.58)** | **US$39.00 (NZ$68.77)** | |
| Total, list, all Standard | US$19.48 | US$112.88 | |

| Jobs per day | L4-hours of cold start a month | NZD | USD |
| --- | --- | --- | --- |
| 4 | 4.06 | 1.66 | 0.94 |
| 12 | 12.18 | 4.99 | 2.83 |
| 48, the most the 30-minute cron can submit | 48.70 | 19.97 | 11.32 |

B submits at most one job per 30-minute tick per environment, and only when the gate passes; a running job keeps leasing, so a burst that arrives mid-run needs no second job. Dev adds its own, smaller GPU line. The 1.5 s per photo is conservative for B, since the GPU job no longer does media prep or row writes. The job asks for 16 GB, above the 4 GB in the L4 example; that difference is not priced (section 8). Supabase is excluded, as it is the same in every approach.

## 6. Technical debt

| Adds | Retires | Leaves in place that should not be there |
| --- | --- | --- |
| One pipeline in two runtimes (Apptainer GPU image, Lambda image) joined by a results contract; the parity test and `inference_contract` check are the guard | Redis twice, ARQ, KEDA and its `42P05` and empty-query gotchas, the dual queue, `pg-conn` | `api_jobs` as one jsonb table for status, locks and now leases, read through unindexed `job_data->>` keys (a ww-backend topic for later) |
| Five lease endpoints and a token to rotate | API-process background jobs that die with an API restart | Training on Google Cloud Run in Singapore; it belongs in a Slurm H100 job like Spyfish's `train_job.sl` |
| Lambda glue: events router, SSM bootstrap, flush middleware, LWA settings | The browser to API to Blob to Drive double transfer, and the Blob metadata underscore stripping | slowapi limits per Lambda environment, so a key's per-minute limit is approximate |
| A login-node crontab under one person's REANNZ account | Drive as archive and its service account shared by dev and prod | `clustering/analyze` still multipart, so capped at the Lambda payload limit |
| Training as a tick-driven state machine | "Set AI flags on the ARQ worker or the pipeline no-ops": the job runs what the manifest lists | The GPU image tag published on GHCR, readable by anyone (the code is already public under GPL) |
| | Re-embedding every crop of a deployment on each upload | |
| | ACR's SKU floor and purge task; the always-on prod replica | |

## 7. Risks and unknowns

| Risk | Guard |
| --- | --- |
| Demo cold start on Lambda is unmeasured | Measure `Init Duration` on dev in step 4; the 5-minute tick keeps one environment warm; provisioned concurrency is the priced fallback |
| A precomputed lookup misses (bbox drift, motion-ROI crops) | Misses are counted per job and alarmed; a missing vector is picked up by the next embed lease automatically; the parity test runs in CI |
| A Lambda limit: 15 minutes, payload, `/tmp` | Parts of 500 photos; exports keep the 4 GB cap in one invocation (measure); training is a state machine; payloads over 200 KB go to `buffer/` |
| Compute nodes cannot reach the API host | Spyfish already reaches S3 from compute nodes (run_pipeline.sl); if the API is blocked, the job writes parts to S3 and the lease runs on `--time` instead of heartbeats |
| L4 queue wait is long | Measured in step 4; the lease protocol makes any GPU a drop-in: an H100 in the same partition, or the report's g6 on AWS Batch in Sydney, runs the same container |
| Reprocessing reads GIR | The confirmation shows the read-back cost; SpeciesNet reprocess needs originals, embedding reprocess reads Supabase crops only |
| API latency to a Sydney Supabase | Measure p95 in step 4; if it regresses, move only the API function to Sydney, accepting the inter-region rate on parse reads |
| Lease token leak | Short presigned URLs, ingest checks parts against leases, rotation is one SSM write; an alarm on lease rate |
| Two image versions disagree | The API tells the cron which tag to run; parts carry the contract version; ingest refuses a mismatch to the DLQ and requeues |
| Bus factor on the crontab | Oldest-queued alarm at 6 h emails; the runbook lets a second project member take over |
| Runaway spend | Reserved concurrency per function (API 10, CPU 5, ingest 2), Slurm `--time` and `singleton`, an AWS budget alert |
| Rollback after prod cutover | The Azure prod API keeps a read-only S3 path for two weeks; AI jobs created on AWS are reaped and re-run |

## 8. Unverified items

| Item | Status | How to check |
| --- | --- | --- |
| Lambda request and response payload limit, async-invoke payload limit, presigned URLs bounded by role session expiry | *memory* | AWS quotas page; a test on dev |
| Function URLs and LWA response streaming in `ap-southeast-6` | *memory*; ARM Lambda prices exist there | deploy step 1 |
| LWA passing non-HTTP events (S3, async invoke, Scheduler) to `/events` | *memory* | deploy step 1; fallback is a plain handler in a second image tag |
| Python container cold start for this image | unmeasured | step 4 |
| hdbscan and umap-learn arm64 wheels | *memory* | `docker buildx` on an arm runner; x86_64 costs 25 % more per GB-s (price list) |
| SQS pollers billing idle requests | *memory* | not used, so not on the bill |
| SSM standard parameters free; EventBridge Scheduler free tier; AWS Budgets free; free tier once per Organization | *memory* | billing console after month one |
| L4 seconds per photo, 120 s cold start, 16 GB needed, price of memory above the 4 GB in the example | unverified | `sacct` and the RSU statement in step 4 |
| REANNZ: allocation class, login-node crontab, Apptainer, compute-node HTTPS, SIF pull on login nodes | unverified | step 0 email |
| GHCR anonymous pulls by Apptainer | *memory* | step 4 |
| Auckland to Sydney round trip per PostgREST call | *memory* | step 4 |
| Supabase region | unknown | dashboard |
| Lambda to Supabase traffic billed as internet egress | assumed, priced at the internet rate | billing after month one |
