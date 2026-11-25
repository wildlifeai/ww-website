# Backend architecture, security and feature flags

The layering rule, the async job system, where secrets may live, and how unfinished work is
gated.

## Backend Architecture Invariant

The backend follows strict layer separation:

```text
routers/
    ↓
domain/
    ↓
services/
```

### Routers

Responsibilities:

* Validate requests
* Call domain logic
* Return responses

May import:

* FastAPI
* Schemas
* Domain modules
* Dependencies

### Domain

Responsibilities:

* Business logic
* Workflow orchestration
* Validation beyond request schemas

May import:

* Schemas
* Services
* Registries

Must not import:

* FastAPI
* Request objects
* Response objects
* HTTP-specific code

### Services

Responsibilities:

* Infrastructure integration
* External APIs
* Supabase
* Azure
* AWS: S3 (`object_store.new_s3_client`) and Lambda invoke (`lambda_invoke`)
* Google Drive
* Model conversion tools


## Async job system

`enqueue_job` in `backend/app/jobs/dispatch.py` picks where a job runs and returns which path it
took:

* `WORKER_BACKEND=lambda` (AWS) first: a job in `BATCH_JOBS` (the GPU kinds) stays queued in
  `api_jobs` with a `job_data.dispatch` record for a REANNZ batch to lease (`"batch"`), and every
  other job is invoked on the `CPU_FUNCTION_NAME` function (`"lambda"`).
* Otherwise, with `REDIS_URL` set, a job not in `IN_PROCESS_JOBS` goes to Redis and the ARQ GPU
  worker (`"arq"`), or runs here if Redis cannot be reached.
* Everything else runs in this process as an asyncio task (`"local"`).

Where each runs, as of 2026-11-26:

* **Cloud dev**, Redis + the ARQ GPU worker are **live** (`ww-embedding-worker-dev`, serverless T4).
* **Production** (the staging tier), the same: `ww-redis` + `ww-embedding-worker` since 26 Jul 2026,
  with the pipeline and Wildlife Brain flags on the worker.
* **AWS `ww-dev`**, `WORKER_BACKEND=lambda`: functions `ww-dev-api` and `ww-dev-cpu`, with
  GPU batches on the REANNZ cluster under Spyfish's `wildlife03546`. No site points at it yet
  ([cloud-infrastructure.md](../../../documentation/resources/cloud-infrastructure.md#aws)).
* **Local**, in-process by default; the ML deps only exist in the `dev` Docker image.

Rules:

* **Every path must keep working.** Never remove the in-process fallback.
* Heavy ML (SpeciesNet, BioCLIP, DINOv3) runs only on the ARQ worker (`--target worker`,
  `requirements-ml.txt`) and the REANNZ batch (`--target gpu`). The Azure API image
  (`--target api`) and the two Lambda functions (`--target lambda`) have no torch, so importing it
  at module scope in code they load breaks them. A dependency for code the `cpu` function runs
  goes in `requirements-lambda.txt`, which adds the CPU clustering stack (hdbscan, umap-learn,
  scikit-learn) to `requirements.txt`.
* The jobs in `IN_PROCESS_JOBS` (`jobs/dispatch.py`: uploads, the CamtrapDP import and export,
  manifests, model conversion and downloads, batch ingest) run in the API process on Azure whatever
  `REDIS_URL` says, so their settings belong on the API. Such a job can be cut off when the API
  replica scales in (dev scales to zero), as uploads and the import already can.
* Status is mirrored to Supabase `api_jobs`, so `/api/jobs/{id}` polling works cross-process. Rely on
  that rather than in-memory state.
* `api_jobs` also holds the run-lock leases (`status = 'lock'`, `services/locks.py`) when there is
  no Redis. A new reader of `api_jobs` filters by status or `job_data->>kind`, never takes every row.
* The AWS path was merged in #442 and is off by default (`WORKER_BACKEND` is `local`). The cluster
  runs only the models; ingest writes the rows with the same pipeline code. Contracts and status:
  [2026-10_aws-reannz-migration](../../../documentation/development%20reports/2026-10_aws-reannz-migration/README.md).
  A model call that bypasses `get_speciesnet_service()`, `get_bioclip_service()` or
  `get_dinov3_service()` breaks that path. A batch job's arguments are read again from its
  dispatch record by the lease (`domain/batch_lease.py`) and by ingest (`jobs/ingest.py`): an
  argument added to `annotate_deployments_job` that those two do not read is silently dropped on
  AWS (`AnnotateRun.of_dispatch` in `jobs/pipeline_run.py` reads its `pipeline` for both).
* On AWS the API sits behind an API Gateway HTTP API: a request must finish within 30 s and a
  response stay under 6 MB, or the caller gets a 503. Work that can take longer goes through
  `enqueue_job`, a large result goes to the object store with a link, a small one (counts,
  warnings) on the job itself (`update_job(result=...)`, read as `JobInfo.result`), and uploads
  arrive as `files_key`; a job reads an upload from the object store, never from the request.
  Work that must stay in the request stops at a time budget and hands the rest back for the
  caller to send again, as iNaturalist publish does (`PUBLISH_BUDGET_SECONDS`,
  `remaining_media_ids`, `domain/inaturalist_publish.py`, #455).
  An image goes out through `domain/media_resolver.serve_media`, which turns an
  original Lambda cannot answer into a redirect; a list that grows with the photos is paged or
  capped (the cluster's `items` pages, `clusters/multi`). The routes that break this today are in
  the migration report's "Left" table; don't add another.
* On Lambda a task left running in the process (`enqueue_local_job`, `asyncio.create_task`)
  stops when the invocation returns, so the job never finishes and the reaper fails it an hour
  later. Code that can run there dispatches through `enqueue_job`, which invokes the `cpu`
  function; `start_thumbnail_backfill` shows the branch. `enqueue_job` lets `create_job`'s
  background write of the `api_jobs` row land before the invoke, so a route never waits for the
  row itself.
* A new light job (no ML) also goes in `dispatch.IN_PROCESS_JOBS`, or on Azure, with `REDIS_URL`
  set, `enqueue_job` sends it to the ARQ worker and cold-starts the GPU for it (`sync_inat_job`,
  #455). Under `WORKER_BACKEND=lambda` the list does not matter: every non-batch job goes to `cpu`.

Live infrastructure detail: `documentation/resources/cloud-infrastructure.md`. Everything under
`documentation/development reports/_archive/` (including `v2-architecture-plan.md`) is **frozen
history**. Read it for *why*, never as a description of current behaviour.


## Security Invariant

The rule for `SUPABASE_SERVICE_ROLE_KEY` and secrets is in
[AGENTS.md](../../../AGENTS.md#non-negotiables). Frontend code uses only anonymous or
authenticated user tokens.

An uploaded ZIP is checked against `domain/zip_limits.py` (`check_members`, `check_size`) before
any member is read or extracted, as the CamtrapDP import and the model upload do: the upload cap
bounds the compressed size only.

## Access checks on service-role routes

A route that reads or writes with the service client bypasses RLS, so it checks the caller
itself with `app/authz.py` (levels and status codes:
[API reference, Access](../../../documentation/resources/api-reference.md#access)):

* A read takes `require_*_access` or `assert_access`.
* A route that changes project data, or starts work on it (a pipeline run, an embed, a cluster
  confirm, a review, an upload), takes `require_*_write` or `assert_write`, and a body list of
  photos goes through `writable_media_ids`. The rule is `_can_write`, ww-backend's
  `has_project_role(..., 'project_member')`; a `project_viewer` is read-only everywhere (UI audit
  decision 1, 21 Oct 2026). `require_not_demo` stays on as well.
* A route that runs as the caller (`get_user_client`) needs neither: RLS decides, and the route
  turns a refused row into `403`.
* Creating a project needs an active organisation role (`project_creator_org_id`).


## Feature flags

The rule, and where an AI flag must be set, is in [AGENTS.md](../../../AGENTS.md#non-negotiables).
A flag is an `FF_*` setting in `backend/app/config.py`, off by default for unfinished work, and a
route whose flag is off answers `FEATURE_DISABLED` instead of running (the website side is in
[frontend.md](frontend.md)).
