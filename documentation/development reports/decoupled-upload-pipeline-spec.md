# Decoupled upload pipeline — durable media rows, resumable backup, dedup & integrity

> **Status:** 📋 Proposal, not scheduled (2026-11-26): **nothing here is implemented**, and it needs a
> decision. Dated 2026-07-26; ww-backend (schema) + ww-website (ingest, sweeper, UI). It assumes Drive
> and Azure Blob: under the AWS migration's approach B
> ([2026-10_aws-reannz-migration](2026-10_aws-reannz-migration/README.md)) uploads go to S3 through
> presigned POSTs and the archive is S3, so its starting point changes.
**Motivating incidents:** production uploads silently discarded for weeks
(`GOOGLE_DRIVE_ENABLED` unset → `media` table empty, newest job 10 Jun);
unassigned photos silently dropped (fixed by triage, PR #99); false-success
dock (PR #99); disabled-backend silence (PR #103).

## 1. The structural problem

Media rows are created **inside** the Google Drive upload job
(`backend/app/jobs/definitions.py`), and only for files that reached Drive.
Storage is therefore a *gate* in front of the database, and every failure
between the browser and Drive makes photos vanish from the app entirely:

| Failure | What the user saw |
|---|---|
| Drive not configured (`settings.GOOGLE_DRIVE_ENABLED=False`) | green tick, 0 media, no job — for weeks |
| Job crash / worker restart mid-batch | some photos exist, the rest gone; no list of which |
| Azure staging blob lost before the job runs | job FAILED, photos unrecoverable server-side |
| Drive quota / permission error on file N | file N gone; nothing marks it for retry |
| Bit-rot / truncated Drive upload | undetected forever (nothing verifies stored bytes) |

The invariant we want instead: **once the bytes reach our server, the photo
exists, permanently and visibly, and everything after that point is a
retryable background step.**

## 2. Current pipeline (as-is)

```
browser ── batches of 10 ──▶ POST /api/exif/parse
                                │  parse EXIF
                                │  store_blob() → Azure staging blob
                                │  (no DB row of any kind)
                                ▼
                        drive-upload job (api_jobs)
                                │  download blobs back from Azure
                                │  preprocess/rename (photo_preprocessing)
                                │  upload to Drive  ── crash here = photos lost
                                │  INSERT media rows (dedup by file_hash)   ◀ rows born here
                                ▼
                          AI pipeline job
```

Existing assets we build on (already in prod):

- `media.file_hash` = `compute_file_hash(bytes, deployment_id)` =
  **sha256(bytes ‖ deployment_id)** — indexed (`idx_media_file_hash`),
  documented as the dedup key, and stamped on the Drive file itself as
  `appProperties.sha256` (`google_drive.py:_find_file_id_by_hash`).
- The drive job's dedup guard (definitions.py ~951): skips files whose hash
  or `gdrive://` path already has a media row.
- Drive-side duplicate skip: upload first queries the parent folder by
  `appProperties.sha256`.

So dedup fundamentals exist; what's missing is *when rows are born* and
*what happens when a step fails*.

## 3. Goals / non-goals

Goals

1. A photo that reaches the server can never silently disappear.
2. Broken uploads resume — user re-drags the same card/folder and only the
   gaps are sent; the server retries its own backlog without user action.
3. No duplicates — re-dragging a fully-uploaded card is a no-op.
4. Corrupted or missing stored copies are detectable and repairable.
5. Backup state is visible (dock, Annotations, per-deployment).

Non-goals

- Changing Drive as the storage provider, folder layout, or the AI pipeline.
- Client-side chunked/parallel upload performance work.
- Video.

## 4. Target pipeline (to-be)

```
browser ── precheck (hashes) ──▶ POST /api/media/precheck   ──▶ "132 done · 4 need upload"
browser ── only the gaps ──────▶ POST /api/exif/parse
                                    │  parse EXIF, sha256
                                    │  store_blob() → staging
                                    │  UPSERT media rows            ◀ rows born HERE
                                    │     backup_status='pending'
                                    ▼
                            backup-sync job (renamed drive job)
                                    │  SELECT media WHERE backup_status='pending'
                                    │  upload to Drive, verify checksum
                                    │  UPDATE row: file_path=gdrive://…,
                                    │             backup_status='synced'
                                    ▼
                              AI pipeline job (unchanged trigger)

          sweeper (scheduled): re-enqueue pending/failed rows older than 15 min
          audit  (scheduled, low freq): verify synced rows against Drive checksums
```

Ingest and backup become independent: a dead Drive config now means rows
pile up in `pending` (visible, counted, retryable) instead of nothing
existing at all.

## 5. Schema changes (ww-backend migration)

```sql
ALTER TABLE public.media
  ADD COLUMN backup_status text NOT NULL DEFAULT 'pending'
    CHECK (backup_status IN ('pending','synced','failed','needs_bytes')),
  ADD COLUMN backup_error text,
  ADD COLUMN backup_synced_at timestamptz,
  ADD COLUMN staging_blob_id text;          -- Azure blob id until synced

-- Backfill: every existing row came from a completed Drive upload.
UPDATE public.media SET backup_status = 'synced', backup_synced_at = updated_at
  WHERE file_path LIKE 'gdrive://%';

-- Dedup becomes a DB guarantee instead of a read-then-insert race.
-- (Pre-flight: verify no existing duplicates; file_hash already embeds
-- deployment_id, but scope the index anyway for clarity.)
CREATE UNIQUE INDEX CONCURRENTLY idx_media_dedup
  ON public.media (deployment_id, file_hash)
  WHERE file_hash IS NOT NULL AND deleted_at IS NULL;

CREATE INDEX idx_media_backup_status ON public.media (backup_status)
  WHERE backup_status <> 'synced';           -- small, hot partial index
```

States:

- `pending` — row exists, bytes in Azure staging, not yet on Drive.
- `synced` — on Drive, checksum verified; staging blob deleted.
- `failed` — last sync attempt errored (`backup_error` says why); sweeper
  retries; distinct from pending only for observability/alerting.
- `needs_bytes` — we have the row but no bytes anywhere (staging blob lost
  before sync, or audit found the Drive copy missing/corrupted). Only a
  re-upload from the user's card can heal this; precheck asks for exactly
  these files.

## 6. Backend changes

### 6.1 `/api/exif/parse` creates rows (routers/exif.py)

After `store_blob()` (~line 541), for every file that has a deployment id:

```python
svc.table("media").upsert({
    "deployment_id": dep_id,
    "file_name": filename,
    "file_path": f"staging://{blob_id}",
    "file_hash": compute_file_hash(content, dep_id),
    "timestamp": exif_timestamp,
    "exif_metadata": exif,
    "uploaded_by": user.id,
    "backup_status": "pending",
    "staging_blob_id": blob_id,
}, on_conflict="deployment_id,file_hash", ignore_duplicates=True).execute()
```

- Upsert-ignore makes re-sending the same file a clean no-op (returns the
  existing row) — the server-side half of resume.
- Uses the service client exactly as the job does today; RLS unchanged.
- Photos with **no** deployment still don't get rows — the triage screen
  (PR #99) already forces that decision before upload, which is the right
  place for it.
- Response gains `media: [{file_name, media_id, deduped: bool}]` so the
  client can count "already existed" precisely.
- **Privacy note (EXIF GPS).** This spec changes *when* `exif_metadata` is
  written, not *who can read it*: the Drive job already writes the full
  EXIF (GPS included) to `media.exif_metadata` today, and rows stay
  RLS-scoped to project members either way. Two invariants to hold:
  ingest-time rows are created only for deployments that passed
  `classify_deployment_access` (same as the job today), and `pending` rows
  must never surface through any public path — they default
  `file_public = false`, and the backups panel/API reads under the same
  RLS as the Annotations grid. The broader question of coarsening GPS on
  any *public* surface predates this spec and is tracked as open
  question 5.

### 6.2 Drive job → backup-sync job (jobs/definitions.py)

The job's candidate list changes from "files in this request payload" to
**"media rows in `pending`/`failed` for these deployments"** (bounded to the
batch's rows by id for normal runs; unbounded for sweeper runs). Per row:

1. Fetch bytes from `staging://` blob. Missing blob → `backup_status='needs_bytes'`, continue.
2. Preprocess/rename (unchanged).
3. Upload to Drive (existing `appProperties.sha256` duplicate check stands).
4. **Verify**: compare the Drive file's `md5Checksum` (request it in
   `fields=`) against an md5 computed from the bytes just uploaded — one
   extra digest, no second download. Mismatch → delete Drive copy, mark
   `failed`, retry next sweep.
5. `UPDATE media SET file_path='gdrive://…', backup_status='synced', backup_synced_at=now(), staging_blob_id=NULL` and delete the staging blob.

Because the work queue is now *derived from the table*, the job is
idempotent and resumable by construction: crash after file 60 of 150 →
next run selects the remaining 90. The existing dedup guard stays during
transition and becomes a safety net.

### 6.3 Sweeper + audit (scheduled)

- **Sweeper** (every ~15 min, cheap partial-index scan): enqueue one
  backup-sync job if any `pending`/`failed` rows are older than 15 min.
  This is what makes "server config was broken for a day" self-healing —
  the moment credentials return, the backlog drains with no user action.
  (The 150-photo backlog from this week would have synced itself the
  moment `google-sa-json` landed.)
- **Audit** (weekly): for `synced` rows, list Drive files' checksums in
  pages and compare; missing/mismatched → `needs_bytes` + surface in the
  backups panel. Catches deletion-behind-our-back and corruption.

### 6.4 Staging retention

Staging blobs live until `synced` (deleted on verify) with a 30-day TTL
backstop. TTL-expired blob for an unsynced row → sweeper marks
`needs_bytes`.

## 7. Resume & dedup from the browser

### 7.1 `POST /api/media/precheck`

Request (after triage/folder-prefix resolution, so deployment ids are known):

```json
{ "files": [ { "file_name": "A63DF680.JPG", "deployment_id": "…",
               "file_hash": "sha256(bytes‖deployment_id)" } ] }
```

Response per file: `new` · `synced` · `pending` (row + staged bytes exist —
don't resend) · `needs_bytes` (row exists, bytes lost/corrupted — resend) ·
`unauthorized` (see below).

**Authorization (mandatory, not an implementation detail).** The endpoint
requires an authenticated session (401 otherwise) and its **first** step is
`classify_deployment_access(user_id, distinct deployment_ids)` — the same
guard `/api/exif/parse` already runs (`app/authz.py`; exif.py builds
`blocked_ids` from it). Files under a `no_access` deployment are answered
`unauthorized` **without the media table ever being queried**, so the reply
is byte-identical whether or not such a row exists — the endpoint cannot be
used as an existence oracle to probe other projects' deployments for known
photos (which would confirm species presence at a location). Defence in
depth: the salted hash means a probe already requires possessing the exact
image bytes *and* the deployment id, but authz must never lean on that.
The client treats `unauthorized` like today's `blocked_deployments`
warning: exclude the files and tell the user. Batch endpoint, one access
check per distinct deployment — no per-file amplification.

Client hashing replicates `compute_file_hash` with WebCrypto
(`crypto.subtle.digest('SHA-256', bytes ‖ utf8(deployment_id))`), run
through a small pool of workers. ~150 × 30 KB files is instant; large
hi-res cards stream at disk speed. Fallback if hashing is slow or WebCrypto
is unavailable: skip precheck and send everything — the server upsert
dedups anyway, so precheck is purely a bandwidth optimisation, never a
correctness requirement.

### 7.2 Upload page UX (`UploadFlow`)

After selection (and triage), one summary line replaces guesswork:

> **136 of 150 already uploaded** · 10 waiting for backup · **4 need
> re-upload** — only the 4 will be sent.

All-done case: "Everything on this card is already uploaded — nothing to
do." Upload button disabled. That is the whole anti-duplication UX: users
can re-drag the same card any number of times, after any failure, and the
system converges.

### 7.3 Surfacing backup state

- **Dock / PipelineStatusBox**: the Drive step becomes "Backed up X of Y";
  terminal state with leftovers reads "10 photos not yet backed up — they
  will retry automatically", warn not error (rows exist; nothing is lost).
- **Annotations**: rows with `backup_status != 'synced'` get a small "not
  backed up yet" chip (the row is already there to render — that's the
  point). `needs_bytes` chip: "re-upload needed — drag the card folder in
  again".
- **Per-deployment backups panel** (Processing history or deployment page):
  counts by status + "Retry backup now" (enqueues sync job immediately
  instead of waiting for the sweeper).

## 8. Rollout

Feature-flagged (`INGEST_CREATES_MEDIA`, default off) so each slice ships
green and the old order keeps working until cut-over:

1. **ww-backend**: migration (columns, backfill, indexes). Inert by itself.
   ⚠ ship respecting the "supabase/** pushes reseed cloud dev DB" rule.
2. **backend**: parse-time upsert behind the flag + job refactor to
   table-derived candidates (works for both row origins during transition).
3. **backend**: precheck endpoint + checksum verify + sweeper/audit.
4. **frontend**: precheck + summary line, dock wording, chips, backups panel.
5. Enable flag on dev → soak with the e2e upload spec (extend it: kill the
   job mid-run, assert resume; re-drag, assert zero duplicates) → prod.

## 9. Open questions

1. **`media_assets` coupling** — thumbnails/derivatives. Do they need a
   parallel `pending` state, or are they generated by the AI pipeline only
   after sync? Determines whether Annotations shows a placeholder or a real
   thumbnail for pending rows (interim: client-side local previews already
   cover the uploading session).
2. **When should the AI pipeline run** — today it follows the Drive job. It
   could run at `pending` (bytes are in staging) for faster results, at the
   cost of running on photos whose backup later fails. Proposal: keep it
   post-sync for now; revisit.
3. **Retention of `needs_bytes` rows** — keep indefinitely as an explicit
   "hole" (recommended: it's the honest record that a photo existed), or
   auto-soft-delete after N days?
4. **Sweeper transport** — reuse `api_jobs` + KEDA (already scales the
   worker) vs. a pg_cron enqueue. Leaning `api_jobs` for uniformity.
5. **EXIF GPS on public surfaces** — `media.exif_metadata` carries raw
   coordinates (taonga-species locations are exactly the data-sovereignty
   case our mātauranga commitments cover). Members-only surfaces are fine
   (RLS), but any surface that honours `file_public = true` — or a future
   share/export — should strip or coarsen GPS unless the project opts in
   (cf. iNaturalist `geoprivacy`). Applies to the **current** pipeline
   identically; raised here so it gets an owner rather than a shrug.

## 10. Why this shape and not alternatives

- **Rows at parse time vs. "job writes rows first, then uploads"**: the job
  can crash before writing anything; the endpoint is the earliest moment
  the server holds the bytes, so that's where durability must begin.
- **Status column vs. inferring from `file_path` prefix**: explicit status
  is indexable, CHECK-constrained, and readable in every query the UI
  makes; the prefix stays as the storage locator only.
- **Client hashes vs. server-only dedup**: server upsert alone already
  guarantees no duplicates — precheck exists so a 2 GB re-drag doesn't
  re-send 2 GB to find that out.
