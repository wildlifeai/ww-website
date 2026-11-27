# Known gotchas

Things that look like bugs in your code and are not. Each one cost somebody time.

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

## RLS denials are silent on reads and updates

A query RLS refuses usually returns **no rows and no error**: a SELECT comes back empty, an
UPDATE changes 0 rows and reports success. Only an INSERT fails loudly (`42501`). So "no error"
proves nothing. A user sees only their own `users` row and their own `user_roles` rows, which is
why the members panel listed only the caller and "removed" members it never touched.

So every update asks for the rows back with `.select('id')` and reports none as a refusal
([frontend.md](frontend.md)); a page that showed "Saved ✓" on a viewer's 0-row update is how the
audit found it (2026-10_ui-audit 03 #1).

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
1,000 of "Sunset test 2"'s 1,101 and never saw the rest (#208), and confirming a cluster of more
than 1,000 photos labelled only 1,000 (2026-10-21). Any backend read over a whole deployment or
project goes through `services/db_utils.read_all`, a query ordered on a unique key and paged with
`.range()` until a short page. A check for known keys looks up only those keys with
`.in_()` in chunks, as the upload dedup in `jobs.definitions.existing_media_keys` does (#317).
The test stand-in `tests/fake_postgrest.FakeDB` returns at most 1,000 rows a read too, so a test
with more rows than that catches a read that does not page (25 Oct 2026).

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

## A composite foreign key with ON DELETE SET NULL nulls `deployment_id` too

`observations` points at `observation_events` and at `media`, and `observation_events` at
`media`, through two-column keys that include `deployment_id`, with `ON DELETE SET NULL`. That
sets every column of the key to NULL, `deployment_id` included, which is NOT NULL, so deleting an
event or a photo that still has rows pointing at it fails the whole delete with `23502`. Clear
the link first: `cluster_deployment_events` sets `observation_event_id` to NULL on the
deployment's observations before it deletes the old events, which until then made every second
clustering of a deployment fail (#458, 22 Nov 2026, checked on a local Postgres 17).
[ww-backend#361](https://github.com/wildlifeai/ww-backend/issues/361) changes the three keys to
`ON DELETE SET NULL (column)`, which keeps `deployment_id`; keep the unlink until it is on every tier.

---

## supabase-py types rows as any JSON value

postgrest types `response.data` as a list of any JSON value (None, a string, a list, ...), so
`row["id"]` or `row.get("x")` on a typed client fails pyright. Read rows through
`rows_of(response)` and a `single()` or `maybe_single()` row through `row_of(response)`, both in
`services/db_utils.py`. Pass `count=CountMethod.exact` (from `postgrest`), not `"exact"`.

---

## Shared Model Lists

Do not invent model names.

Reuse existing model registries and constants rather than duplicating configuration.

---

## Shared Test Users

Do not hardcode credentials.

The seeded test users, and where their credentials are kept:
[testing-with-seed-users.md](../../../documentation/resources/testing-with-seed-users.md).

---

## Cloud dev is reseeded without notice

The linked dev Supabase project is reset from `ww-backend`'s seeds whenever that repo's
`deploy_cloud_projects.yml` runs for dev, which a merge into its `dev` that touches `supabase/**` or
`deployment_scripts/**` starts (a docs-only merge does not). A reset takes about 10 minutes (01:51
to 02:00 UTC on 2026-11-26, when the demo login was briefly invalid and the CI e2e failed): a red
e2e run in that window is not a regression, re-run it after. Rows you created by hand (a model
uploaded through the registry path, a deployment, a project pointed at a model) can be gone the next
morning. Scripts that set up a test must be re-runnable, reports must record ids as evidence rather
than as things to rely on, and a "missing row" is a reseed until proven otherwise (2026-09-05: the
`20V1` Person Detection built in step 1 of the person-detection runbook disappeared this way). A
LoRaWAN bench test loses its camera, its deployment, its messages and any organisation ingest token
with each reset; what to redo is in
[lorawan-dev-testing.md](../../../documentation/resources/lorawan-dev-testing.md).

---

## The shared Supabase service client is one HTTP/2 connection

`create_service_client()` returns a process-wide cached client (see
`services/supabase_client.py`). A request handler and a background job (the Drive upload job
runs in-process on the dev image) share that connection. When the server drops it, both see
`ConnectionTerminated` at once. On 2026-09-05 that cost an upload batch its Drive job while the
request still returned 200. On Lambda the server closes it while the environment sits frozen, so
the first request after a pause fails with `Server disconnected` (AWS dev, 2026-11-20: 17 job
status writes skipped in one upload test). Callers that can safely repeat a call catch
`is_dropped_connection(exc)` (`services/supabase_client.py`), call `reset_service_client()` and
retry once, as the Drive job enqueue (`routers/exif.py`) and the job store's reads and writes
(`jobs/store.py`) do. Do the same for any new call that runs beside a background job or on
Lambda, and never let a transport error surface as a silent success.

---

## Supabase Storage refuses any file over 50 MB

Both Supabase projects are on the Free plan, whose upload limit is 50 MB for every bucket,
whatever the bucket's own limit says. The CamtrapDP export stored its ZIPs there and failed
past about 50 photos (#387). A file that can grow, like an export, goes to the object store
instead: `services/object_store.upload_export` streams it (Azure Blob today, S3 on AWS) and
`domain/camtrapdp_export.download_link` makes its read-only link.

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

## Cloudflare Pages builds are check runs, not GitHub deployments

Cloudflare reports a build as a `Cloudflare Pages` check run and creates no deployment, and it
builds only `dev` and `main`. Every GitHub deployment here comes from an Actions job with
`environment:`, so a `deployment_status` trigger fires on backend deploys: `e2e.yml` tested the
dev preview after them for a month while its header promised every pull request's preview
(found 26 Oct 2026). A workflow that needs a build's URL reads the check run, as
`e2e-preview.yml` does; the detail is in the deployment guide's
[Preview Deployments](../../../documentation/resources/deployment-guide.md#preview-deployments).

---

## A Supabase channel name is reused while it is still leaving

`supabase.channel(name)` returns the client's existing channel of that name, and
`removeChannel` leaves it only when the server answers. An effect that removes a channel and
subscribes again under the same name (its deps changed, say another project ticked) gets the
leaving channel back: the new `.on()` bindings are dropped as duplicates and `.subscribe()` does
nothing, so the page stops getting changes with no error. Give each subscription its own name, as
`hooks/useFieldLive.ts` does (read in `@supabase/realtime-js` 2.118, 26 Nov 2026).

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
deployment or folder. `media_registry.group_bursts`, the pipeline's evidence fusion, the batch
lease's part edges, the labeller and the eval call it. Today's
firmware spaces burst frames 3 to 5 s apart, so a tighter gap splits real bursts.

---

## Every model keeps its own observation row

SpeciesNet, Gemini, BioCLIP and Camera AI each write their own `observations` rows; no step
edits another model's row. The final presence verdict is a derived `source_type='consensus'`
row (`evidence_fusion_v1`). Per-crop classification follows it too: its row sits on the
detection's box beside SpeciesNet's (#162).

Each model's scores need their own floor too. The run's `confidence_threshold` is SpeciesNet's box
cutoff (0.2); BioCLIP read it until 27 Nov 2026 and wrote a dog crop as a mite at 0.21 (#433), so
it now has `BIOCLIP_MIN_SCORE` (`pipeline.bioclip_min_score`). A new classifier gets its own.

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

## S3 in Auckland needs the regional endpoint

`ap-southeast-6` is an opt-in region, and the global `<bucket>.s3.amazonaws.com` host does not
serve its buckets. boto3 presigns against that global host unless the client has an
`endpoint_url`, so a browser's presigned POST fails with `net::ERR_FAILED` and no CORS answer.
`services/object_store.new_s3_client` sets `https://s3.<region>.amazonaws.com` with
virtual-hosted addressing; create every S3 client through it.

---

## An `s3://` original is read only as part of its own photo

`media_resolver.resolve_media` and `serve_media` take the photo's row as
`media=MediaRef.of(row)` (its `id` and `deployment_id`), and read an `s3://` path only from its own
folder: `originals/<project>/<deployment>/` in `S3_BUCKET`, the layout `object_store.original_key`
writes, where the project is the deployment's or one it was moved from (`deployment_moves`; a move
leaves the files where they were). Without the row, or for any other key or bucket, the answer is
the one for a missing original, with a `media_s3_original_refused` warning naming the photo. A new
reader passes the row, a crop its photo's row. An Azure API reads `s3://` originals only when its
`S3_BUCKET` names the bucket.

---

## A presigned S3 URL dies with the Lambda role that signed it

On Lambda the S3 client signs with the function role's temporary credentials, and a presigned
URL stops working when that session ends, whatever its `ExpiresIn` says (#462). Hand presigned
URLs out for minutes (`S3_PRESIGN_GET_SECONDS`, 300 s). A link that must last longer, like an
export's 24 hours, is the API's own HMAC-signed route, which redirects to a fresh presigned URL
on each open (`domain/camtrapdp_export.download_link`). That route is used only once
`EXPORT_LINK_SECRET` is set (`settings.export_link_signed`); without it the export's link is a
plain presigned GET and dies with the role.

---

## A form takes at most 1,000 fields

Starlette parses a form with `max_fields=1000` and `max_files=1000` and answers `400` "Too many
fields" above either, before the route runs. Keys sent by S3 upload (`files_key`) are fields, so
`/api/exif/parse` by key, with a `paths` field per photo and one option, stops at 499 photos under
the Azure cap of 500 (`MAX_UPLOAD_IMAGES_PER_REQUEST`). On Lambda the cap is 25 a call
(`LAMBDA_MAX_IMAGES_PER_REQUEST` in `routers/exif.py`, #457), and the website sends 10 a request
(checked 22 Nov 2026). Size a key endpoint's cap by its fields, not its photos.

---

## Gemini API key limits

`GEMINI_API_KEY` in the root `.env` is on the free tier: 15 requests per minute and 500 per
day per model, and no Batch API. Run `backend/scripts/eval_presence.py`, from `backend/`, with
`--min-interval 4.2` and `--cache` (resumable, never pays twice); a full run of the 700-frame labelled set spans two daily windows, and `--only` runs a subset.
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

The same rewrite breaks other commands in Git Bash: `aws ssm put-parameter --name /ww/dev/X` fails with
"must be a fully qualified name", and `docker run --tmpfs /tmp` with "mount path must be absolute".
Prefix them the same way.

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
