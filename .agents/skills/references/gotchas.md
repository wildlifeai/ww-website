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
[03-DATA-AND-SYNC](../../../documentation/onboarding/03-DATA-AND-SYNC.md)).

---

## A PostgREST read stops at 1,000 rows without saying so

Supabase caps every response at 1,000 rows, and a query without `.range()` just gets the first
1,000 with no error. A deployment can hold more photos than that: the thumbnail backfill read
1,000 of "Sunset test 2"'s 1,101 and never saw the rest (#208). Any backend loop over a whole
deployment pages with `.order()` plus `.range()` until a short page, as
`media_registry.backfill_thumbnails` does. A check for known keys looks up only those keys with
`.in_()` in chunks, as the upload dedup in `jobs.definitions.existing_media_keys` does (#317).

---

## A camera has one open deployment, checked at commit

`deployments_one_open_per_device` (ww-backend #320) allows one deployment per device with no
`deployment_end` that is not soft-deleted. It is deferred, so the write that breaks it fails when
its PostgREST request commits, with `23P01`. The details carry the device id only for the service
role: under a user session RLS hides the key, so they read "Key conflicts with existing key."
Anything that inserts an open deployment on an existing device, or clears `deleted_at` or
`deployment_end`, can hit it. `app/domain/open_deployments.py` recognises it and names the camera
(with a service-role lookup when the details lack the id): the restores answer `409`, the
CamtrapDP import gives the deployment its own placeholder device.

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

## Search the open branches before writing a helper

Grep the open PR branches, not only `dev`, for a function before adding one. In September 2026
parallel branches produced four burst groupers, two TFLite readers and two train/test split
functions before a review merged them (#155, #158).

```bash
gh pr list --state open --json headRefName -q '.[].headRefName' | while read b; do
  git grep -n "def group_bursts" "origin/$b" -- backend/ ; done
```

---

## One burst grouper

Frames of one trigger are grouped only by `domain/burst_evidence.py::group_bursts`: firmware
sequence tag first, EXIF timestamp gap otherwise (`BURST_GAP_SECONDS`, 10 s), never across a
deployment or folder. `media_registry.group_bursts`, the labeller and the eval call it. Today's
firmware spaces burst frames 3 to 5 s apart, so a tighter gap splits real bursts.

---

## Every model keeps its own observation row

SpeciesNet, Gemini, BioCLIP and Camera AI each write their own `observations` rows; no step
edits another model's row. The final presence verdict is a derived `source_type='consensus'`
row (`evidence_fusion_v1`). Per-crop classification follows it too: its row sits on the
detection's box beside SpeciesNet's (#162).

---

## A pipeline step writes nothing beside a human verdict

A run picks its photos when it starts and a reviewer can label one while the model works, so a
step that inserts `observations` filters its rows through `pipeline.without_human_verdicts` just
before the insert, and deletes superseded rows only for the photos it kept. Runs on one deployment
are serialised by `run_pipeline`'s lock (`services/locks.py`); call `run_pipeline` rather than a
step directly, or two runs pick the same photos again (#284).

---

## `consensus_approved` means human truth

`active_learning` treats `review_status='consensus_approved'` as a human verdict. Machine rows,
the consensus row included, use `ai_reviewed`.

---

## Gemini API key limits

`GEMINI_API_KEY` in the root `.env` is on the free tier: 15 requests per minute and 500 per
day per model, and no Batch API. Run `scripts/eval_presence.py` with `--min-interval 4.2` and
`--cache` (resumable, never pays twice); a full run of the 700-frame labelled set spans two daily windows, and `--only` runs a subset.
To re-score what is cached (the stratified tables, a new SpeciesNet dump), add `--cache-only`: no
call is made and uncached frames stay unanswered.

---

## Committed data files carry relative paths

A labels CSV or result cache committed beside a report must not contain a machine path
(`C:\Users\...`). Write paths relative to the export folder; `eval_presence.py --root` resolves
them.

A SpeciesNet dump written in the dev Docker image keys its frames `/photos/...`; pass
`--speciesnet-root /photos` with `--root <export folder>`. In Git Bash prefix the command with
`MSYS_NO_PATHCONV=1`, or MSYS rewrites `/photos` to a path under the Git install, nothing
matches and every SpeciesNet frame comes back unanswered.

---

## GitGuardian flags a test password with a literal prefix

A per-run password such as `` `pw-${crypto.randomUUID()}` `` trips the Generic Password
detector (#159). Generate test passwords with no literal part (`crypto.randomUUID()`), and mark
a genuine false positive in the GitGuardian dashboard rather than ignoring a path.

---

## The React compiler lint can skip a whole component without a word

The `react-hooks` compiler rules (`set-state-in-effect`, `refs`, `purity`) silently skip a
component they cannot analyse: one carrying an `eslint-disable` for `exhaustive-deps`, or one
calling `Intl.DateTimeFormat()` during render. Removing that disable in `MediaDetail` surfaced
three errors it had hidden (#184); the `Intl` call hid `ProjectDefaultsPanel` (#137). The symptom
is a `react-hooks/*` disable directive reported as unused. Keep such calls at module scope, and
treat that warning as a component that stopped being checked, not a line to delete. One skip is
loud: a callback that reads a `useMemo` declared further down the component is an error,
`preserve-manual-memoization`, and the fix is to declare the memo, and what it reads, above the
callback (`UploadFlow`'s upload handler, #269).
