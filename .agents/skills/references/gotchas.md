# Known gotchas

Things that look like bugs in your code and are not. Each one cost somebody time.

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
