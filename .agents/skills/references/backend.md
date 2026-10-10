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
* Google Drive
* Model conversion tools

Never place business logic in routers.

Never import FastAPI inside domain modules.


# 7. Async Job System Rules

## Current State

Job dispatch in `backend/app/jobs/dispatch.py` takes the first route that is configured:
`CLOUD_RUN_JOB_NAME` starts one Cloud Run job execution (`python -m app.jobs.cloudrun_entry`,
the Google Cloud pilot), then `REDIS_URL` enqueues to the ARQ worker, then the job runs in
process. A route that fails falls through to the next. The two older routes:

```text
REDIS_URL set             REDIS_URL empty
Client                    Client
  ↓                         ↓
API                       API
  ↓                         ↓
Redis queue               in-process asyncio task
  ↓                         ↓
ARQ GPU worker            (same process)
  ↓                         ↓
Supabase api_jobs  ←──────────┘  (status mirrored either way)
```

Where each runs, as of 2026-07:

* **Cloud dev**, Redis + the ARQ GPU worker are **live** (`ww-embedding-worker-dev`, serverless T4).
* **Production**, API-only; no worker, no Redis. AI does not run there yet.
* **Local**, in-process by default; the ML deps only exist in the `dev` Docker image.

Rules:

* **Every route must keep working.** Never remove the in-process fallback.
* To ask whether AI leaves this process, use `settings.job_offload_configured` (Cloud Run job or
  Redis), never `settings.REDIS_URL` alone, or the upload job runs the AI on the lean API image.
* A job must be in `definitions.JOBS` (the ARQ worker and `cloudrun_entry` run only those), and
  its arguments must be JSON-serialisable: the Cloud Run route sends them as JSON.
* Heavy ML (SpeciesNet, BioCLIP, DINOv3) belongs in the worker, the lean `--target api` image has no
  ML deps, so importing torch at API module scope breaks production.
* Feature flags gating ML must be set on the **worker**, not just the API.
* Status is mirrored to Supabase `api_jobs`, so `/api/jobs/{id}` polling works cross-process. Rely on
  that rather than in-memory state.
* `api_jobs` also holds the run-lock leases (`status = 'lock'`, `services/locks.py`) when there is
  no Redis. A new reader of `api_jobs` filters by status or `job_data->>kind`, never takes every row.

Live infrastructure detail: `documentation/resources/cloud-infrastructure.md`. Everything under
`documentation/development reports/_archive/` (including `v2-architecture-plan.md`) is **frozen
history**. Read it for *why*, never as a description of current behaviour.


## Security Invariant

Treat the service-role key as backend-only infrastructure.

Never:

* Expose `SUPABASE_SERVICE_ROLE_KEY` to frontend code
* Store secrets in source control
* Use service-role access in browser code
* Bypass authentication dependencies

Frontend code must only use anonymous or authenticated user tokens.


# 6. Feature Flag Rules

Experimental or incomplete features should be gated.

When adding a feature that is:

* Experimental
* Incomplete
* Environment-specific
* Under active development

Create a feature flag in:

```text
backend/app/config.py
```

and gate registration appropriately.

Do not expose unfinished functionality by default.
