# 03 — Data, Supabase & the Job System

How the web app reads and writes data, the security model it operates under, and how long-running
work is run as background jobs.

## Two paths to data

1. **Direct Supabase (most reads + observation writes).** The browser holds a Supabase session
   (anon key + the user's JWT → the **`authenticated`** Postgres role) and queries tables directly
   with `supabase.from('…')`. Row-Level Security (RLS) scopes every row to the user's projects.
2. **Backend API (privileged / heavy work).** EXIF parsing, Drive uploads, model conversion, the AI
   pipeline, LoRaWAN ingestion, and admin-only RPCs go through FastAPI, which uses the
   **service-role** key (bypasses RLS) where appropriate.

## The RLS + GRANT model (read this before debugging "permission denied")

Postgres checks permissions in **two layers, in order**:

1. **Table privileges (`GRANT`)** — failing this gives `permission denied for table <name>`.
2. **Row-Level Security policies** — failing this gives `new row violates row-level security policy`.

A table-level `GRANT` must exist **before** RLS policies are even consulted. A common failure mode:
a table has RLS policies for `INSERT`/`UPDATE` but only `GRANT SELECT … TO authenticated`, so writes
fail with *permission denied for table* even though the policy would have allowed the row.

> [!CAUTION]
> The schema, RLS policies, **and** table GRANTs are owned by the [`ww-backend`](https://github.com/wildlifeai/wildlife-watcher-backend)
> repo. The web app never alters them. To enable a new client-side write, add a `ww-backend`
> migration that grants the privilege to `authenticated` **and** a matching RLS policy.
> Example (observations): `GRANT INSERT, UPDATE ON public.observations TO authenticated;` plus a
> `FOR UPDATE … project_member` policy so reviewers — not just admins — can confirm/correct labels.

`has_project_role(uid, project, role)` is **hierarchical**: `project_admin` satisfies a
`project_member` check. Use `project_member` in policies for actions reviewers should perform.

**Schema files ≠ live database.** A GRANT present in the `ww-backend` baseline can still be missing
from an environment created before it (prod `media_assets`, Jul 2026: every Annotations query died
with *permission denied* because a PostgREST embed aborts the whole request). When debugging
permissions, verify against the **live** DB, not just the migrations.

## Core tables the web touches

| Table | Used by | Access |
|-------|---------|--------|
| `projects`, `deployments` | Insights, EXIF matching, Drive folders | RLS (+ service-role) |
| `media` | Annotations grid + modal | RLS (read; uploads via backend) |
| `media_assets` | embedded in `media` queries (renditions: provider, dimensions, bytes) | RLS read — a missing GRANT aborts the **whole** embedding query (prod, Jul 2026) |
| `observations` | Annotations modal (confirm/correct/blank/box/add) | RLS — `authenticated` needs INSERT/UPDATE GRANT |
| `taxa` | SpeciesPicker (local search) | RLS read |
| `user_roles`, `project_invitations` | members panel, invitation banner | RPCs only, via `frontend/src/lib/projectMembers.ts` (see below) |
| `media_embeddings`, `embedding_runs`, `annotation_runs` | Wildlife Brain / provenance | service-role |
| `devices`, `lorawan_*`, `firmware`, `ai_models`, `api_jobs` | LoRaWAN, manifests, models, jobs | service-role |

**Project membership goes through RPCs, never direct queries.** RLS lets a user read only their
own `users` row and their own `user_roles` rows, and it turns an unauthorised UPDATE into
"0 rows changed" with no error. The members panel was built on direct queries, so it listed only
the caller, failed every add and reported removals that never happened. It now uses
`get_project_members`, `send_project_invitation`, `get_project_pending_invitations`,
`remove_project_member`, `cancel_project_invitation`, `get_my_pending_invitations` and
`respond_to_invitation`, all through `frontend/src/lib/projectMembers.ts`. Adding a member is an
invitation the invitee accepts from the banner under the nav (or in the mobile app); an admin can
cancel it while it is pending. The server compares emails case-insensitively
(ww-backend#216), and `toMembersError` maps the SQLSTATEs the RPCs raise.

`frontend/src/lib/projectMembers.integration.test.ts` runs that module against a **local**
`ww-backend` stack as real signed-in users (invite, accept, decline, cancel, remove, the refusals). Run it
whenever a `ww-backend` change touches roles, invitations or RLS; the header of the file has the
two commands. Without the `WW_TEST_*` variables it skips, so `npm test` stays offline.

Observation provenance fields (`source_type`, `review_status`, `reviewer_id`, `annotator_id`,
`classification_method`) are written through one helper, `frontend/src/lib/observations.ts`, so
every surface records review state consistently. See [05-ANNOTATION-WORKFLOW](./05-ANNOTATION-WORKFLOW.md).

## Frontend ⇄ backend env mapping

`frontend/vite.config.ts` loads the **root** `.env` and exposes a subset to the browser:

| Root `.env` | Frontend `import.meta.env` |
|-------------|----------------------------|
| `SUPABASE_URL` | `VITE_SUPABASE_URL` |
| `SUPABASE_ANON_KEY` | `VITE_SUPABASE_ANON_KEY` |
| `VITE_API_BASE_URL` | `VITE_API_BASE_URL` |

The **service-role key is never exposed to the browser** — it stays in the backend.

## Async job system

Heavy tasks (Drive uploads, model conversion, pipeline runs) run as **in-process `asyncio`
background tasks** — no Redis required for local dev. Container deploys can switch to ARQ + Redis
via `jobs/worker.py`. When no Redis is reachable, enqueue logs
`redis connection error … arq_enqueue_failed_fallback_local` and the job falls back to the
in-process runner — expected noise in local dev, not a failure.

```
create_job() → queued → processing → completed
                              └────────→ completed_with_errors | failed
```

| Status | Meaning |
|--------|---------|
| `queued` | created, waiting for the runner |
| `processing` | actively executing |
| `completed` | success — result available |
| `completed_with_errors` | partial success (some files failed) |
| `failed` | error — details in `error` |

- **Persistence**: in-memory dict (fast path) + async sync to the Supabase `api_jobs` table (durable).
- **Crash recovery**: on boot, any `processing` jobs in Supabase are marked `failed`.
- **Frontend**: polls `GET /api/jobs/{id}` (~2s); responses carry ordered `events[]` for incremental
  UI updates. The global `UploadContext` keeps progress alive across navigation via `ProgressDock`.

## Image upload pipeline

Dragging camera images into the website runs this end-to-end. There is one upload surface: the
`/upload-data` page (`UploadDataPage` → `components/upload/UploadFlow.tsx`). The header **Upload**
button, Home and the three-step guide all link there. (Until Sep 2026 two uploaders coexisted, a
modal opened from the header and the older `AnalyseImages` page behind `/upload-data`; they had
drifted, and the modal was too small for the triage step, so both were folded into the page.)
**Images always sync to Google Drive**, the old "Sync to Google Drive" toggle was removed; Drive is
the default long-term store. AI analysis runs after upload by default and can be switched off per
upload (`run_ai`); for CamtrapDP imports it is opt-in.

> [!IMPORTANT]
> Media rows are created **inside the Drive job, only for files bound to a deployment**. Two
> consequences drive the client design below: photos without a deployment are never stored anywhere,
> and a disabled/broken Drive backend means *nothing* is stored. The planned fix is
> [decoupled-upload-pipeline-spec](../development%20reports/decoupled-upload-pipeline-spec.md)
> (media rows at ingest + resumable backup sync).

### In the browser (`UploadFlow` → `UploadContext`)

The staged selection (files, card paths, EXIF ids) is page state: it lives in `UploadFlow` until
`startUpload` takes it, so leaving or reloading the page drops it. A `beforeunload` guard warns
before a reload does, and **Change selection** is the explicit way out. Reading the EXIF heads of a
large card takes a moment; the page shows a "Reading photos… N of M" count under the summary tiles
and the Upload button waits for it (the deployment count resolves by card folder until then).

1. **Deployment resolution, EXIF first.** Every WW500 frame carries the full deployment UUID in
   EXIF tag `0xF200`; the browser reads it from each file's head (`lib/exifDeploymentId.ts`) and
   matches it exactly against the user's deployments. Only when a frame carries no tag does the
   card folder (`MEDIA/<8-hex>/`, a prefix of the same id) decide. The folder can be wrong: it is
   created at boot, before the deployment id is configured, so a frame under `MEDIA/00000000/`
   can carry the real id in its EXIF (ww-website#140).
2. **Triage of unassigned photos** (`UnassignedTriage`). Files that resolve to no deployment are
   grouped into **capture sessions** — same EXIF id, else same card folder, gaps under 6 h
   (`unassignedSessions.ts`) — and shown with sample thumbnails, time-span stats and, when the
   camera stamped one, the deployment id it named. Per session the user assigns an existing
   deployment, creates one from the photos (`POST /api/deployments`, **under the stamped id**
   when there is one, so the phone that configured the camera converges on the same row when it
   syncs), or skips it. **Skipped photos are not uploaded** and the screen says so; before triage
   existed they were silently dropped (Jul 2026). On the page the sessions render as a responsive
   grid of cards (`components/upload/upload.css`, 96 px thumbnails, one card per session) rather
   than a single scrolling column.

   Triage is the **only** way photos get assigned. The older blanket "assign everything to one
   deployment" form is gone: it was gated on the `/validate` verdict for **card-folder prefixes**,
   which the EXIF-first resolution had already made irrelevant, so a card whose frames all matched
   by their stamped id still demanded a project and a deployment because the folder
   (`MEDIA/00000000/`) matched nothing. Answering it created a real, orphaned deployment row that
   no photo then used. Whether a photo needs assigning is now one question, asked in one place:
   `unresolvedFileIndices` in `unassignedSessions.ts`.
3. **Batch planning** (`UploadContext.startUpload`). Files are ordered by deployment and cut into
   batches of ≤ 10 that **never span two deployments**, so a batch's `assigned_deployment_id`
   cannot mislabel a mixed batch. Triaged photos join the optimistic Annotations grid and the
   post-upload redirect filter like folder-resolved ones. The redirect carries every deployment
   the upload touched (`?deployment=a,b,c`), so the deployment pill reads "N deployments" rather
   than focusing the first.
4. Each batch → `POST /api/exif/parse`; job polling and the dock live in `UploadContext`.
5. **Optimistic cards** (`lib/pendingCards.ts`). The Annotations grid shows a local preview per
   uploaded file until its media row exists. The server renames frames on the way in
   (`A9BC8A30.JPG` becomes `20260905194608_01.jpg`), so a name match never retires a card;
   instead each real row created since the upload started retires one card of the same
   deployment. Cards are not counted as media or as "no image" in the header stats.

### Failure surfacing (no false success)

`derivePhase` returns **`failed`** — the dock can no longer end green when nothing was stored:

- a batch request throws → `failedFiles` + `uploadError` (per-batch catch in `startUpload`);
- the server refuses storage → the response's `drive_upload.enabled === false`
  (e.g. `GOOGLE_DRIVE_ENABLED` unset → `reason: "server_disabled"`) is logged as an error and
  fails the run. Production ran exactly this way for weeks while the dock showed a green tick
  (Jul 2026), the incident this branch guards against;
- the server took the batch but could not start its Drive job → `drive_upload.status === "error"`
  counts the whole batch as failed and surfaces the reason in the dock. On the 2026-09-05 bench
  run a batch lost its job to a dropped Supabase connection and only a log line said so.

Two server-side retries cover the transient failures seen on that run: a single Drive frame
upload is retried up to three times on a timeout, dropped connection or HTTP 429/5xx
(`services/google_drive.py`, `DriveTransientError`), and the Drive job enqueue is retried once
with a fresh Supabase client after an HTTP/2 `ConnectionTerminated` (`routers/exif.py`).

> [!NOTE]
> Docker trap: the dev compose bind-mounts `./service-account.json` and points
> `GOOGLE_SERVICE_ACCOUNT_JSON` at it. If that file does not exist when the container is first
> created, Docker creates an empty **directory** in its place and every Drive job fails with
> "points to a file that does not exist". Write the credential from `.env` to that path, then
> `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --force-recreate api`.

### On the server

```
POST /api/exif/parse  → parse EXIF, bind deployment (EXIF Deployment_ID, else card-folder prefix;
                        `deployment_id_source` says which), buffer bytes to Azure blob store,
                        enqueue upload_drive_images_job
upload_drive_images_job:
  DOWNLOAD → PREPROCESS (rename/sort into project/deployment folders)
  → DRIVE_UPLOAD     google_drive.upload_analysis_images — hash-dedup skips files already in Drive
  → REGISTER MEDIA   insert `media` rows (file_path = gdrive://<id>, file_hash) so images appear in
                      the Annotations grid and the pipeline has something to run on
                      (EXIF timestamps are normalised "YYYY:MM:DD HH:MM:SS" → ISO before insert —
                       raw EXIF is rejected by Postgres and would silently create 0 media rows)
  → AUTO-ANNOTATE    enqueue auto_annotate_deployments (the AI pipeline, async) — see 04-AI-PIPELINE
  → CLEANUP          delete the Azure blobs; job completes
```

### Backend environment requirements (per environment)

| Var | Notes |
|-----|-------|
| `GOOGLE_DRIVE_ENABLED` | Defaults to **`False`** — unset means the API parses EXIF, stores **nothing**, and reports `enabled: false`. |
| `GOOGLE_DRIVE_FOLDER_ID` | Root Drive folder for that environment's archive. |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | ACA secret `google-sa-json`. The Drive folder must be shared (**Editor**) with the service-account email or uploads 404 at write time. |

Both ACA apps have all three since **2026-07-26** (prod was missing all of them — the root cause of
"empty `media` table in production"). Dev and prod currently share one service account
(`ww-drive-uploader@ww-drive-upload-photos.iam.gserviceaccount.com`); a prod-only account is planned
so key rotation can't take down both.

**Idempotency guards** (so re-uploads / partial uploads are safe):
- **Guard 1 — media dedup + self-heal:** Drive upload hashes content (`appProperties.sha256`) and
  skips duplicates, returning the *existing* file id. Media registration then dedups by
  `media.file_hash` **or** `gdrive://` path (no duplicate rows), and **back-fills a `media` row for
  any image that's in Drive but has no DB row yet** — so re-uploading recovers stranded images.
- **Guard 2 — annotate only un-annotated media:** the auto-trigger runs `only_unannotated=true`, so
  it processes only images without a Cloud AI observation (see [04-AI-PIPELINE](./04-AI-PIPELINE.md)).

> **Local dev gotcha:** the Drive credential file is mounted by the **dev** compose, so always start
> the API with both files: `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d`.
> Starting with only the base compose drops the mount and the upload job fails to authenticate.

## Timezones & capture time

Camera EXIF timestamps are **UTC**, and `media.timestamp` (a `timestamptz`) stores that exact UTC
instant — never local wall-clock. To show users "the time where the photo was taken", we format that
instant in the **deployment's** timezone at display time.

- **`deployments.timezone`** — an IANA zone name (e.g. `Pacific/Auckland`), owned by the `ww-backend`
  schema (display-only; `media.timestamp` stays UTC). It is **app-populated** (no DB trigger):
  `resolve_timezone(lat, lon)` in [`domain/photo_preprocessing.py`](../../backend/app/domain/photo_preprocessing.py)
  derives it from the deployment's GPS via `timezonefinder`. CamtrapDP import sets it automatically;
  existing/device deployments are filled by `POST /api/deployments/backfill-timezones` (idempotent).
- **Display** — [`frontend/src/lib/time.ts`](../../frontend/src/lib/time.ts) (`formatCaptureTime`,
  `getTimeOfDay`, `hourInTimezone`) renders the UTC instant in the deployment zone (with a label like
  `10:44 am NZST`) and drives the day/night filter. **Store the IANA name, not a fixed offset**, so DST
  is handled automatically (NZ is +12 in winter, +13 in summer).
- **Graceful fallback** — when `timezone` is `NULL` (or the column isn't deployed yet) the UI falls
  back to the **viewer's browser** zone, i.e. the previous behaviour. Deployment queries fetch the
  column defensively (retry without it) so the app keeps working during the schema rollout.

> **Why not store local time in the DB?** A `timestamptz` is an absolute instant; putting local
> wall-clock in it loses the instant, breaks cross-deployment sorting, mishandles DST, and corrupts
> CamtrapDP / Darwin Core export. One source of truth (UTC) + a per-deployment zone is the correct model.

## Supabase resources expected

- **Storage buckets**: `firmware`, `ai-models` (private); `media-renditions` (public — thumbnails/previews, see [04-AI-PIPELINE](./04-AI-PIPELINE.md)).
- **Auth providers**: GitHub + Google OAuth.
- **Realtime**: enable on `lorawan_parsed_messages` for live mobile updates.
