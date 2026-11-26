# AWS and REANNZ migration, approach B

> **Status:** 🔧 Active, 2026-11-26: approach B is in `dev` ([pull request #442](https://github.com/wildlifeai/ww-website/pull/442), merged 2026-11-20) behind settings whose defaults keep today's Azure path. Every fix under "Left" that blocked the dev cutover is merged; the export link ([#462](https://github.com/wildlifeai/ww-website/issues/462)) and the deploy role ([#468](https://github.com/wildlifeai/ww-website/issues/468)) still need their steps on AWS, and the GBF report ([#456](https://github.com/wildlifeai/ww-website/issues/456)) counts in the database and waits for its timing on AWS dev. The `ww-dev` stacks run on AWS and two dev batches ran on the REANNZ cluster under Spyfish's `wildlife03546`; no site points at either yet. Next: point the dev site at `ww-dev` (step 3), the 4-hourly crontab and two measuring weeks (step 4), then production ([checklist](#production-checklist-steps-5-and-6)).

The design is [approach-b.md](approach-b.md), the proposal ranked first on 31 October 2026. The estate it replaces is [cloud-infrastructure.md](../../resources/cloud-infrastructure.md); the Google Cloud plan it supersedes is [2026-09_gcp-pilot-and-migration](../_archive/2026-09_gcp-pilot-and-migration/README.md).

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

Every setting, its default and its value on AWS: [deployment-guide.md, AWS and the REANNZ batch](../../resources/deployment-guide.md#aws-and-the-reannz-batch). On AWS the stack sets the backends, bucket and function names; every other value, secret or not, is an SSM parameter under `/ww/<env>/`.

## Contracts

**Lease routes** (`/api/worker/*`, bearer lease token, not in the OpenAPI document):

| Route | Body | Answer |
|---|---|---|
| `GET /pending` | | `jobs`, `photos` (upper bound), `oldest_minutes`, `submit`, `image`, `time_limit_minutes`, `contract` |
| `POST /lease` | `max_photos`, `slurm_job_id`, `paged` | the manifest, or `{"lease_id": null}` |
| `GET /lease/{id}/items/{job}` | `offset`, `limit` (query, at most 1,000) | a paged job's photos from `offset`, URLs signed now |
| `POST /lease/{id}/heartbeat` | `done`, `total` | `cancel`, `expires_at` |
| `POST /lease/{id}/part` | `job_id`, `part` | `method`, `url` (S3) or `path` (the API), `headers` |
| `PUT /lease/{id}/part/{job}/{n}` | gzipped part | for the azure and local backends |
| `POST /lease/{id}/part/complete` | `job_id`, `part` | starts `ingest_results_part_job` |
| `POST /lease/{id}/done` | `jobs: [{job_id, parts_total, failed_media}]` | releases the lease, starts `finalize_batch_job` per job |
| `GET /lease/{id}/media/{media_id}?crop=` | | one original or hero crop through the SSRF-guarded resolver; a 307 to a presigned copy when it is too large to answer |

**Manifest**: `lease_id`, `contract`, `expires_at`, `heartbeat_seconds`, `part_max_photos`, and per job `job_id`, `kind` (`annotate` or `embed`), `models` (`speciesnet`, `bioclip`, or `dinov3` with the model name), `config` (threshold, SpeciesNet cutoffs, `per_detection`, BioCLIP rank and labels), `items` (`media_id`, `deployment_id`, and `url` for S3 or a public rendition, else `path`), or in a paged lease `items_total`, the photos then read page by page.

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
| Five lease endpoints | Nine routes (above) | A part PUT for non-S3 backends and a media route, so `gdrive://` originals reach the cluster without a raw URL or a credential |
| Lease state in `job_data` | A lease row in `services/locks.py` plus marker objects | A process with a stale memory copy of a job (the job store is memory-first) could overwrite `job_data` fields; rows and markers it never writes |
| `results/{env}/...` | `results/{lease}/{job}/...` | One bucket per environment already |
| `originals/{org}/{project}/{deployment}/{sha256}` | `originals/{project}/{deployment}/{file_hash}.jpg` | The upload payload carries no organisation; `file_hash` is `google_drive.compute_file_hash` (bytes and deployment id), the existing dedup key |
| Incremental embedding | Not built | Not needed for correctness: an embed lease sends all the deployment's crops, as `embed_and_cluster_deployment` re-embeds them today. Ingest no longer downloads the crops |
| HMAC export link | Built after the branch ([pull request #490](https://github.com/wildlifeai/ww-website/pull/490)): `GET /api/exports/camtrapdp/{job_id}/download`, a 307 to a fresh presigned GET, once `EXPORT_LINK_SECRET` is set; a plain presigned GET until then | A presigned GET dies with the Lambda role's session; see "Left" for the steps on AWS |
| Training as a tick-driven state machine | Not built | See "Left" |
| Accounts not specified | One AWS account per environment (`ww-dev`, later `ww-prod`) in wildlife.ai's organization; `ww-shared` deploys in each, its role trusting only that account's GitHub environment (parameter `Env`, `dev` or `prod`; `prod` deploys from the `staging` environment) | A dev mistake cannot reach prod, and the management account holds no workloads |
| Compose `gpu` profile replaces Redis and the ARQ worker | `batch-infer` added to the `gpu` profile, inert without `WW_WORKER_TOKEN` | The Azure path stays until teardown |
| The `api` function on a Function URL with response streaming | An API Gateway HTTP API (`$default` route to the `api` function), the Web Adapter in buffered mode | Function URLs do not exist in `ap-southeast-6` (found at the first deploy, 2026-11-20; Victor chose this over a REST API or a Sydney API). A request must finish in 30 s and a response stay under 6 MB; the routes that cannot are under "Left" |
| A budget in the environment stack | One organization-wide budget, `wildlife-ai-monthly`, set by hand in the management account ([cloud-infrastructure.md](../../resources/cloud-infrastructure.md#how-to-keep-aws-clean)) | CloudFormation in `ap-southeast-6` has no `AWS::Budgets::Budget`, and a member account cannot create budgets until the payer account enables them |

On the hero-crop BioCLIP path (`FF_PER_CROP_CLASSIFY_ENABLED` off) a Gemini box that outranks every SpeciesNet box gives a hero the cluster did not crop; that photo gets no BioCLIP row and the part counts a miss. The per-crop path, on in production, matches exactly (parity tests).

## Left, not in this branch

| Item | Effect until done | Where |
|---|---|---|
| Export link that outlives the Lambda role session: the HMAC route is in `dev` ([pull request #490](https://github.com/wildlifeai/ww-website/pull/490)); `/ww/dev/EXPORT_LINK_SECRET` is not set in SSM yet, and a link is to be opened again the next day on AWS dev | Until the secret is set, an emailed export link can stop working within hours on AWS | `backend/app/routers/exports.py`; [#462](https://github.com/wildlifeai/ww-website/issues/462) |
| An export with originals on AWS dev, now that the export reads `s3://` originals (PR #494, in `dev`) | Not yet checked on AWS: the photos uploaded there should be in the ZIP | [#493](https://github.com/wildlifeai/ww-website/issues/493) |
| `GET /api/reports/gbf` on AWS dev for a multi-year, multi-project selection, now that the database counts the photos: the route calls [ww-backend#362](https://github.com/wildlifeai/ww-backend/issues/362)'s `gbf_report_counts` as the caller instead of reading every photo of the period | Not yet timed on AWS: cloud dev has no photos to time it against, and the local measure is 3.6 s for 294,000 photos ([ww-backend pull request #382](https://github.com/wildlifeai/ww-backend/pull/382)). The function reaches the production database only when ww-backend promotes `dev`, so the website's release of the route waits for it | `backend/app/domain/gbf_report.py`; [#456](https://github.com/wildlifeai/ww-website/issues/456) |
| `train_species_brain_job` as a batch job on the REANNZ cluster, where training is planned (camera models, per-project species models, larger fine-tunes on an A100; 2026-11-20) | Can exceed the 15-minute limit on the `cpu` function; keep `FF_MODEL_TRAINING_ENABLED` off on AWS | `backend/app/jobs/definitions.py`, `backend/app/jobs/dispatch.py`; [#463](https://github.com/wildlifeai/ww-website/issues/463) |
| `reprocess_project_job` and `reprocess_all_job` fanned out to per-deployment embed batches | On AWS they reach the `cpu` function, which cannot embed, and fail with a clear message | `backend/app/jobs/dispatch.py`; [#464](https://github.com/wildlifeai/ww-website/issues/464) |
| Drive archive copy and path rewrite (`migrate_archive_job`, step 7) | `gdrive://` rows keep resolving through the media route | [#465](https://github.com/wildlifeai/ww-website/issues/465) |
| Grid grouping asks for every photo of the deployments shown, capped at `MULTI_CLUSTER_MAX_PHOTOS` (50,000) | A larger request is refused rather than answered past 6 MB | [#474](https://github.com/wildlifeai/ww-website/issues/474) |
| Finished `api_jobs` rows deleted after 90 days by the stale-job reaper (agreed in [ww-backend#364](https://github.com/wildlifeai/ww-backend/issues/364)) | The table grows by a row at every sign-in and every *Run AI again* | `backend/app/jobs/store.py`; [#486](https://github.com/wildlifeai/ww-website/issues/486) |
| The e2e upload test's cleanup deletes its rows but not its originals in the bucket | Test originals pile up under `originals/` in `wildlifewatcher-dev-media` | `e2e/cleanup.mjs`; [#470](https://github.com/wildlifeai/ww-website/issues/470) |
| `scripts/parity_audit.py`, `scripts/fetch-env.sh` for SSM and Lambda | Azure-only until then | [#466](https://github.com/wildlifeai/ww-website/issues/466) |
| A test of the flush middleware on a Lambda-mounted app | Covered by reading only | `backend/app/main.py`; [#467](https://github.com/wildlifeai/ww-website/issues/467) |
| Least-privilege deploy role and pinned actions: in `dev` ([pull request #491](https://github.com/wildlifeai/ww-website/pull/491)); `ww-shared` not redeployed with it yet, and `deploy-aws.yml` has not run | The narrower role is untested until both have run | `infra/aws/shared.yaml`, `.github/workflows/deploy-aws.yml`; [#468](https://github.com/wildlifeai/ww-website/issues/468) |
| The new REANNZ project's code in `cluster/reannz/*` (`--account` and the `/nesi/project` and `/nesi/nobackup` paths), and in the cluster layout and crontab below | They name Spyfish's `wildlife03546` until REANNZ assigns the code | `cluster/reannz/`; [#469](https://github.com/wildlifeai/ww-website/issues/469) |

Merged into `dev` on 2026-11-22 and 2026-11-23, and so off this list: the routes that worked inside the request (pipeline run [#452](https://github.com/wildlifeai/ww-website/issues/452), CamtrapDP import [#454](https://github.com/wildlifeai/ww-website/issues/454), iNaturalist publish and sync [#455](https://github.com/wildlifeai/ww-website/issues/455), event grouping [#458](https://github.com/wildlifeai/ww-website/issues/458); image clustering [#453](https://github.com/wildlifeai/ww-website/issues/453) was retired instead), responses under 6 MB [#457](https://github.com/wildlifeai/ww-website/issues/457), the job-status write retry [#459](https://github.com/wildlifeai/ww-website/issues/459), and the batch-wait wording [#460](https://github.com/wildlifeai/ww-website/issues/460).

## ww-backend

| Change | Issue | State (2026-11-26) |
|---|---|---|
| Deleting an event or photo clears only its link (`ON DELETE SET NULL (column)`) | [ww-backend#361](https://github.com/wildlifeai/ww-backend/issues/361) | merged into ww-backend `dev` |
| GBF counts in the database (`gbf_report_counts`) | [ww-backend#362](https://github.com/wildlifeai/ww-backend/issues/362) | merged into `dev` ([pull request #382](https://github.com/wildlifeai/ww-backend/pull/382), 2026-11-25); the website's side is [#456](https://github.com/wildlifeai/ww-website/issues/456) |
| `media.file_path` can be an `s3://` key | [ww-backend#363](https://github.com/wildlifeai/ww-backend/issues/363) | merged into `dev` |
| `api_jobs` indexes for the website's reads; finished jobs kept 90 days | [ww-backend#364](https://github.com/wildlifeai/ww-backend/issues/364) | merged into `dev`; the website's deletion is [#486](https://github.com/wildlifeai/ww-website/issues/486) |
| `media_assets.storage_provider` value `aws_s3` | not filed | only if the column should be filled; the website leaves it NULL (`backend/app/domain/media_registry.py`) |

None reaches the production database until ww-backend promotes `dev` to staging.

## Migration steps

From [approach-b.md](approach-b.md), section 4, in approach A's order as the review recommends.

| # | Step | Status | Rollback |
|---|---|---|---|
| 0 | A REANNZ project for Wildlife Watcher (allocation class, login-node crontab, compute-node HTTPS to the API host, memory above the L4 bundle, a service account for the job); Supabase region; AWS account, Auckland enabled, Lambda quota | Requested from Victor's REANNZ login, separate from Spyfish's `wildlife03546` (2026-11-20): to July 2027, about 900 L4 hours (600 inference, 300 training), 50 A100 hours, 200 GB project storage, 100 GB scratch. On 2026-11-23 REANNZ asked whether the GPU jobs are a live service; they are not (scheduled batch jobs, outbound HTTPS only), and the answer asks whether the check should run from a login-node crontab or `scrontab`, since REANNZ's [scron page](https://docs.nesi.org.nz/Batch_Computing/scron/) does not say whether a scron job may call `sbatch`. AWS done: Lambda quota 1,000 (2026-11-20); compute nodes reach the API over HTTPS (2026-11-21). Supabase region not read | none |
| 1 | `infra/aws/shared.yaml` (`Env=dev`), then `ww-dev` from `infra/aws/template.yaml` with bucket `wildlifewatcher-dev-media`, in the `ww-dev` account; SSM parameters; the account's budget | 2026-11-20: `ww-shared` and `ww-dev` deployed from a workstation, `/health` 200 on the stack's `ApiUrl`; the organization's budget `wildlife-ai-monthly` set. What runs there now: [cloud-infrastructure.md](../../resources/cloud-infrastructure.md#aws) | delete the stacks |
| 2 | Code behind flags into `dev` | merged 2026-11-20, [pull request #442](https://github.com/wildlifeai/ww-website/pull/442) | revert |
| 3 | Dev API on Lambda; Pages preview `VITE_API_BASE_URL`; `ALLOWED_ORIGINS` | The fixes are in `dev` (see "Left"). Next: deploy `ww-dev` from `dev`, set `/ww/dev/ALLOWED_ORIGINS` with the dev site first (the iNaturalist callback returns to the first entry), then the dev preview's `VITE_API_BASE_URL` to the stack's `ApiUrl`. CORS already accepts every `*.ww-website.pages.dev` origin | Pages preview env back to the ACA FQDN |
| 4 | Dev cluster: SIF, weights, token, crontab; two weeks measuring seconds per photo, cold start, queue wait, RSU, ingest time, precomputed misses | Two trials on 2026-11-21 under `wildlife03546`, run by Victor through OnDemand: SpeciesNet over 57 photos in 50 s on one L4 (peak 1.4 GB), 58 observations ingested within 90 s; DINOv3 in 34 s (peak 0.9 GB); at 2 CPUs and 8G the billing weight fell from 84 to 62. The cluster checks every 4 hours, not 30 minutes (Victor, 2026-11-23): the crontab line below and `BATCH_CRON_INTERVAL_MINUTES` 240, with `BATCH_MAX_WAIT_MINUTES` 0 proposed so each check takes whatever waits and the website says "usually within 4 hours" (60 plus 240 says 5). Crontab not set; the measuring not started | comment out the crontab line |
| 5 | `ww-prod`; the Azure prod API gets the `s3://` read path (in `dev`, live with the next release), `S3_BUCKET` and a read-only key | not started; [checklist](#production-checklist-steps-5-and-6) | delete the stack |
| 6 | Prod cutover | not started; [checklist](#production-checklist-steps-5-and-6) | Pages env back |
| 7 | Drive archive copy | not built, [#465](https://github.com/wildlifeai/ww-website/issues/465) | reverse run |
| 8 | Teardown of `WW-AE` and the Azure, Redis, ARQ, KEDA and Drive code | not started | none |

## Production checklist (steps 5 and 6)

`prod` is the live website: AWS account `ww-prod`, GitHub environment `staging`, the prod/staging Supabase project.

Before anything is created:

1. Step 4's two measuring weeks with no new failure.
2. A `dev` to `main` release. It gives the Azure production API the `s3://` read path behind today's defaults, and puts `deploy-aws.yml` on `main`, the only branch `workflow_dispatch` runs from.
3. ww-backend's `dev` promoted to staging, with the changes above.
4. [#456](https://github.com/wildlifeai/ww-website/issues/456) and [#493](https://github.com/wildlifeai/ww-website/issues/493) merged, [#462](https://github.com/wildlifeai/ww-website/issues/462) and [#468](https://github.com/wildlifeai/ww-website/issues/468) closed after their steps on AWS dev; [#466](https://github.com/wildlifeai/ww-website/issues/466) and [#469](https://github.com/wildlifeai/ww-website/issues/469) wanted.

Then, each with Victor's yes:

5. The `ww-prod` account as in [Adding an AWS account](../../resources/cloud-infrastructure.md#adding-an-aws-account): Auckland on, the Lambda concurrency quota at 1,000 (about a day), the Identity Center assignment, a budget.
6. `ww-shared` with `Env=prod` (its deploy role trusts the GitHub environment `staging`); the `staging` environment's `AWS_DEPLOY_ROLE_ARN`, `ECR_REGISTRY` and `AWS_REGION`.
7. SSM `/ww/prod/`: the prod Supabase values; the flags as on Azure production and the keys they need; a new lease token's SHA-256; an `EXPORT_LINK_SECRET` of its own; `ALLOWED_ORIGINS` with the production site first; its own Google service account (dev and production share one today); the demo account; `GENERAL_ORG_ID`.
8. `deploy-aws.yml` with target `prod`; `/health` 200 on its `ApiUrl`.
9. On the cluster: `~/.config/ww/prod.token`, `prod.api` and the prod crontab line.

Cutover (step 6): freeze deploys; let the Azure production queue drain; set the Pages production `VITE_API_BASE_URL` to the prod `ApiUrl`; check `/health`, *Try the demo* and one upload with cloud AI. Rollback: the Pages variable back to the Azure address, and redeploy.

## Setting up step 1 and step 4 by hand

| What | Where |
|---|---|
| Lease token: `python -c "import secrets; print(secrets.token_urlsafe(32))"`; its SHA-256 to `/ww/<env>/WORKER_TOKEN_SHA256` | the raw token only in `~/.config/ww/<env>.token` on the cluster, mode 600, uploaded by Victor with OnDemand's Files |
| API URL for the cron | `~/.config/ww/<env>.api`, the stack's `ApiUrl` output |
| Every other setting and secret | SSM SecureString `/ww/<env>/<NAME>` |
| Signing in to AWS | Identity Center, the `ww-dev` profile: [cloud-infrastructure.md](../../resources/cloud-infrastructure.md#signing-in-to-aws) |
| Variables for `deploy-aws.yml` | Per GitHub environment (`dev`, `staging`), because each is its own AWS account: `AWS_DEPLOY_ROLE_ARN`, `ECR_REGISTRY`, `AWS_REGION` (`ap-southeast-6`). Repository-wide: `AWS_DEPLOY_ENABLED`, `AWS_BUCKET_DEV` (`wildlifewatcher-dev-media`), `AWS_BUCKET_PROD` (`wildlifewatcher-prod-media`), `AWS_ALARM_EMAIL` |
| The first `ww-dev` deploy | `workflow_dispatch` only runs a workflow that exists on the default branch (`main`), so until a release carries `deploy-aws.yml` there, step 1 runs from a workstation: Docker with buildx for the arm64 `lambda` image (`--platform linux/arm64 --provenance=false`; about 15 minutes emulated, most of it building `hdbscan`), the SAM CLI, and the `ww-dev` profile. `sam deploy` needs `--image-repository <registry>/ww-backend`. On Windows, `docker login` to ECR fails in Credential Manager because the token is too long: push with a temporary `DOCKER_CONFIG` whose `config.json` already holds the registry's `auths` entry, then delete it |
| GHCR package | public since 2026-11-21 (an organization owner first allows public packages), and `ww-website` has Write under Manage Actions access so `deploy-aws.yml` can push |
| Cluster layout and access | [cloud-infrastructure.md](../../resources/cloud-infrastructure.md#reannz); the scripts in `cluster/reannz/` go to `bin/` |
| First pull and weights | `sbatch --export=ALL,IMAGE=<image>,SIF=<sif> bin/ww_pull.sl` (32 GB, about 20 minutes, a 3.6 GB SIF), then `bin/ww_cache_models.sh <sif>`, which asks for `HF_TOKEN` (DINOv3 is gated) and keeps it in that one job |
| A test run by hand | The API asks for a job only at `BATCH_MIN_PHOTOS` (100) queued photos or `BATCH_MAX_WAIT_MINUTES` (60), so a test submits the GPU job directly: `sbatch --job-name ww-<env>-infer --time 20 --export=ALL,SIF=<sif>,WW_API=<api>,WW_ENV=<env>,WW_GPU_IMAGE=<image> bin/ww_infer.sl` |
| Crontab | `0 */4 * * * /nesi/project/wildlife03546/ww/bin/ww_cron.sh <env> >> /nesi/project/wildlife03546/ww/logs/cron-<env>.log 2>&1` |
