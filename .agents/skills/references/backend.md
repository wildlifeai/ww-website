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

Job dispatch has **two paths**, chosen at runtime by `REDIS_URL` in `backend/app/jobs/dispatch.py`:

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

* **Both paths must keep working.** Never remove the in-process fallback.
* Heavy ML (SpeciesNet, BioCLIP, DINOv3) belongs in the worker, the lean `--target api` image has no
  ML deps, so importing torch at API module scope breaks production.
* Feature flags gating ML must be set on the **worker**, not just the API.
* Status is mirrored to Supabase `api_jobs`, so `/api/jobs/{id}` polling works cross-process. Rely on
  that rather than in-memory state.

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
