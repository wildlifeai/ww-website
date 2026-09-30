---

name: ww-website-agent
description: >
Essential workflow, repository awareness, and architecture rules for any agent interacting with the
Wildlife Watcher website. Read and follow this skill before making frontend, backend, API,
infrastructure, or documentation changes.
-----------------------------------------

# Wildlife Watcher Website Agent

The `ww-website` repository is a multi-service platform consisting of:

* React + Vite frontend
* Python FastAPI backend
* Shared Supabase data layer
* Image analysis and AI tooling
* LoRaWAN telemetry ingestion
* Firmware and manifest management workflows

> **Canonical Documentation**
>
> Always consult the relevant documentation before making assumptions:
>
> * `readme.md`
> * `documentation/onboarding/02-CODEBASE-GUIDE.md`
> * `documentation/onboarding/03-DATA-AND-SYNC.md`
> * `documentation/resources/api-reference.md`
> * `documentation/resources/deployment-guide.md`
> * `documentation/resources/cloud-infrastructure.md` — what actually exists in Azure/Supabase/Cloudflare
> * `documentation/README.md` — the index; says which docs are **living** vs **frozen history**
> * `.agents/DESIGN.md` — design system (colors, typography, spacing, component patterns) for any UI work or screen generation
>
> This skill defines architectural guardrails and decision rules. Detailed implementation guidance belongs in documentation.

---

## Development conversations and documentation

**Docs are the record, GitHub issues are the tracker.** The rules live in
[`documentation/development reports/README.md`](../../documentation/development%20reports/README.md),
including how to file a finding and what to check before closing a thread. Read it before
starting or closing one.

Never leave substantive material only in a chat transcript, an email or a PR comment. An
investigation, a review exchange or a design decision belongs in a dated report. This is the
failure mode to watch for, because the work is done and the finding is real, and it
evaporates anyway because it only ever existed in a conversation.

---

# 1. Critical Invariants

These rules are non-negotiable.

## Database Ownership Invariant

The website does not own the database schema.

All database schema changes must originate in:

```text
ww-backend/supabase/schemas/
```

Never:

* Create tables from this repository
* Modify columns from this repository
* Create database functions from this repository
* Treat this repository as the source of truth for schema

If a schema change is required:

1. Make the change in `ww-backend`
2. Follow the backend schema workflow
3. Update this repository only after the schema exists

---

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

---

## Security Invariant

Treat the service-role key as backend-only infrastructure.

Never:

* Expose `SUPABASE_SERVICE_ROLE_KEY` to frontend code
* Store secrets in source control
* Use service-role access in browser code
* Bypass authentication dependencies

Frontend code must only use anonymous or authenticated user tokens.

---

## Documentation Invariant

When behavior changes:

* Update the relevant documentation
* Keep documentation and implementation synchronized
* Update route documentation when routes change
* Update API documentation when contracts change

Do not create duplicate documentation when existing documentation can be updated.

---

# 2. Repository Ecosystem Awareness

Before making changes, consider downstream impacts.

| System                            | Relationship                                 |
| --------------------------------- | -------------------------------------------- |
| `ww-website`                      | Website frontend and backend                 |
| `ww-backend`                      | Database schema source of truth              |
| `wildlife-watcher-mobile-app`     | Shares the same Supabase database            |
| `Seeed_Grove_Vision_AI_Module_V2` | Owns EXIF output and LoRaWAN payload formats |
| `ww-hardware`                     | Owns CONFIG.TXT firmware expectations        |

---

# 3. Cross-Repository Ownership Rules

Before changing any shared contract, identify the owning repository.

| Concern                               | Owner             |
| ------------------------------------- | ----------------- |
| Database schema                       | `ww-backend`      |
| EXIF metadata format                  | Firmware          |
| LoRaWAN payload format                | Firmware          |
| CONFIG.TXT structure                  | `ww-hardware`     |
| Shared storage usage                  | Backend ecosystem |
| Shared deployment/device/project data | Backend ecosystem |

Do not modify externally-owned contracts without coordination.

---

# 4. Frontend Architecture Rules

Preferred flow:

```text
pages
  ↓
components
  ↓
hooks
  ↓
apiClient
```

Rules:

* Use `apiClient` for backend communication
* Use TanStack Query for server state
* Keep route components thin
* Prefer reusable components
* Prefer hooks for complex state management
* Avoid direct `fetch()` calls when `apiClient` already provides the functionality
* Avoid duplicating API response parsing logic

Frontend should focus on presentation and user interaction rather than business logic.

---

# 4a. File Format Rules

## Line Endings — LF everywhere

**Every file in this repository uses LF (`\n`).** Never CRLF, in any file type, on any platform.

Rules:

* Write new files with LF
* Preserve LF when editing — do not let an editor or tool convert a file to CRLF
* `.gitattributes` enforces this (`* text=auto eol=lf`); do not add per-file overrides

Windows gotchas that silently rewrite a whole file to CRLF — avoid them:

* **PowerShell `Out-File` / `Set-Content` / `>`** — these write CRLF and, in Windows PowerShell 5.1,
  also mis-decode UTF-8 on the way in. Never round-trip a file through PowerShell to edit it. Use the
  editing tools, or `python`/`sed` if scripting.
* **`sed -i` on a CRLF working-tree file** strips the CR and rewrites the file as LF — correct here, but
  it shows as a whole-file diff if the blob was CRLF. Check `git diff --numstat` after any bulk edit: a
  content change of a few lines that reports hundreds of changed lines is a line-ending rewrite, not your
  edit.

Verify before committing:

```bash
# any CRLF file under a path?
grep -rlU $'\r' documentation/ || echo "all LF"

# did a bulk edit rewrite whole files?
git diff --numstat
```

Note that `core.autocrlf=true` on a dev machine makes the *working tree* CRLF regardless; the
`.gitattributes` rule is what keeps the committed blobs LF. Prefer `core.autocrlf=false` locally so what
you see is what is stored.

## Encoding — UTF-8, no BOM

Docs and source contain non-ASCII characters (te reo Māori macrons, arrows, emoji status markers).
Preserve them. Mojibake such as `â€"` or `Ã©` means a tool decoded UTF-8 as ANSI — revert the file and
redo the edit with a UTF-8-safe tool rather than hand-repairing it.

---

# 5. Environment Rules

Environment configuration is centralized.

Rules:

* Root `.env` is shared by frontend and backend
* Do not create `frontend/.env.local`
* Do not commit `.env`
* Do not commit credentials
* All backend environment variables must be defined in `backend/app/config.py`

The application should fail fast when required configuration is missing.

## Docker trap: a bind-mount source that does not exist

`docker-compose.dev.yml` bind-mounts `./service-account.json` and points
`GOOGLE_SERVICE_ACCOUNT_JSON` at it, overriding the inline JSON in `.env`. If that file is
missing when the container is first created, Docker silently creates an empty **directory**
in its place and every Drive job then fails with "points to a file that does not exist",
while the upload request itself still returns 200. Write the credential from `.env` to that
path (it is gitignored), then recreate the container:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --force-recreate api
```

A restart is not enough: the mount is resolved at container creation.

---

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

---

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

* **Cloud dev** — Redis + the ARQ GPU worker are **live** (`ww-embedding-worker-dev`, serverless T4).
* **Production** — API-only; no worker, no Redis. AI does not run there yet.
* **Local** — in-process by default; the ML deps only exist in the `dev` Docker image.

Rules:

* **Both paths must keep working.** Never remove the in-process fallback.
* Heavy ML (SpeciesNet, BioCLIP, DINOv3) belongs in the worker — the lean `--target api` image has no
  ML deps, so importing torch at API module scope breaks production.
* Feature flags gating ML must be set on the **worker**, not just the API.
* Status is mirrored to Supabase `api_jobs`, so `/api/jobs/{id}` polling works cross-process. Rely on
  that rather than in-memory state.

Live infrastructure detail: `documentation/resources/cloud-infrastructure.md`. Everything under
`documentation/development reports/_archive/` (including `v2-architecture-plan.md`) is **frozen
history** — read it for *why*, never as a description of current behaviour.

---

# 8. Known Architectural Gotchas

## user_roles Uses scope_id

Do not assume:

```text
organisation_id
```

exists on `user_roles`.

The table uses:

```text
scope_id
scope_type
```

Always verify schema details in `ww-backend`.

---

## API Responses Use a Standard Envelope

Backend responses follow:

```json
{
  "data": {},
  "error": null,
  "meta": {}
}
```

Frontend code should expect and preserve this structure.

Do not invent alternate response formats.

---

## Verify Schema Before Writing Queries

Never guess:

* Table names
* Column names
* Constraints

Verify against the actual backend schema before writing queries.

---

## RLS denials are silent on reads and updates

A query RLS refuses usually returns **no rows and no error**: a SELECT comes back empty, an
UPDATE changes 0 rows and reports success. Only an INSERT fails loudly (`42501`). So "no error"
proves nothing. A user sees only their own `users` row and their own `user_roles` rows, which is
why the members panel listed only the caller and "removed" members it never touched.

For anything about other users (members, invitations, roles), call the `ww-backend` RPC that
checks the caller itself; `frontend/src/lib/projectMembers.ts` wraps the membership ones. After a
write, refetch and show what the database holds rather than updating local state optimistically.
When a `ww-backend` change touches roles, invitations or RLS, run
`frontend/src/lib/projectMembers.integration.test.ts` against a local stack (see
[03-DATA-AND-SYNC](../../documentation/onboarding/03-DATA-AND-SYNC.md)).

---

## Shared Model Lists

Do not invent model names.

Reuse existing model registries and constants rather than duplicating configuration.

---

## Shared Test Users

Do not hardcode credentials.

Consult the backend seed documentation when test users are required.

---

## Cloud dev is reseeded without notice

The linked dev Supabase project is reset from `ww-backend`'s seeds whenever that repo's
workflow runs. Rows you created by hand (a model uploaded through the registry path, a
deployment, a project pointed at a model) can be gone the next morning. Scripts that set up a
test must be re-runnable, reports must record ids as evidence rather than as things to rely
on, and a "missing row" is a reseed until proven otherwise (2026-09-05: the `20V1` Person
Detection built in step 1 of the person-detection runbook disappeared this way).

---

## The shared Supabase service client is one HTTP/2 connection

`create_service_client()` returns a process-wide cached client (see
`services/supabase_client.py`). A request handler and a background job (the Drive upload job
runs in-process on the dev image) share that connection. When the server drops it, both see
`ConnectionTerminated` at once. On 2026-09-05 that cost an upload batch its Drive job while the
request still returned 200. Callers on that path retry once after `reset_service_client()`
(`routers/exif.py`); do the same for any new call that runs beside a background job, and never
let a transport error surface as a silent success.

---

## Object URLs and React StrictMode

Create and revoke an object URL (`URL.createObjectURL`) inside the **same** effect. StrictMode,
which the dev server runs, unmounts and remounts effects once on mount without re-running
state initialisers, so a URL created in `useState(() => ...)` and revoked in the cleanup points
at a revoked blob for the life of the component. Every triage thumbnail rendered as a broken
image for that reason until #142.

---

## "Closes #N" on a PR to `dev` does not close the issue

GitHub auto-closes only on merge to the default branch. PRs here target `dev`, so the issue
stays open until `dev` reaches `main`. Close it by hand when the PR merges, or expect it on the
open list for a while (#140 after #141).

---

# 9. Validation Requirements

Before committing:

## Backend Changes

Run:

* Ruff linting
* Ruff formatting
* Pytest

## Frontend Changes

Run:

* ESLint
* TypeScript validation: `npx tsc -b --noEmit`, **not** `npx tsc --noEmit`. The root
  `tsconfig.json` is references-only (`"files": []`), so plain `tsc --noEmit` type-checks
  nothing and exits 0 with errors present. `npm run build` (`tsc -b && vite build`) catches
  them; so does the `-b` form on its own.
* Production build if applicable

## General

Verify:

* Documentation is updated
* Feature flags are respected
* Shared contracts remain compatible
* No secrets were introduced

Do not commit changes that fail validation.

---

# 10. Development Principles

Prefer:

* Thin routers
* Small domain functions
* Reusable components
* Strong typing
* Explicit validation
* Real API testing
* Follow YAGNI (You Aren't Gonna Need It) principles
* Prefer simple, one-liner solutions where readable and appropriate

Avoid:

* Business logic in routers
* Duplicated logic
* Hardcoded configuration
* Architecture assumptions
* Unverified schema assumptions

---

# Agent Self-Check

Before making a change, verify:

### Database

* Am I avoiding schema changes in this repository?
* Have I checked ownership of the affected data model?

### Backend

* Am I respecting `routers → domain → services`?
* Have I avoided HTTP imports in domain code?

### Frontend

* Am I using existing hooks and apiClient abstractions?
* Am I avoiding duplicated API logic?

### Security

* Am I keeping service-role access backend-only?
* Have I avoided exposing secrets?

### Documentation

* Does documentation need updating?
* Have I updated it alongside the code?

### Validation

* Have I run the appropriate tests and linting?

If any answer is "no", stop and correct the issue before proceeding.
