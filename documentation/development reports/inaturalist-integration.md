# iNaturalist Integration — Design & Implementation

> **Status:** Phases 1–4 implemented (2026-06-08), behind `FF_INAT_ENABLED`. Schema in `ww-backend`;
> the tables exist on dev (checked 25 Oct 2026). The sync-back updates the badge only and writes no
> identifications ([§6](#what-happens-today-checked-25-oct-2026-123)).
> **Owners:** web platform.

Lets a Wildlife Watcher user link their **personal iNaturalist account**, publish reviewed
camera-trap observations to it, see per-photo upload/ID status on thumbnails, and have the
**community identifications flow back** into the WW database.

---

## 1. End-to-end flow

```
Connect (OAuth2 + PKCE)                    Publish                         Sync back
─────────────────────       ──────────────────────────────       ────────────────────────────
WW user ──▶ /api/inat/auth   select photos in Annotations         scheduled / "↻ Sync IDs"
        ──▶ iNat consent     ──▶ POST /api/inat/publish            ──▶ POST /api/inat/sync
        ──▶ callback stores   ── consolidate bursts (Δt)            ── batch-poll public iNat API
            encrypted token   ── drop by-catch (human/vehicle/blank)── quality_grade + community_taxon
                              ── create obs + proxy-upload photos    ── update inat_observations (badge)
                              ── record inat_observations(+media)    ── write consensus obs into WW
```

Three external token types are used (all handled in `services/inat_oauth.py`): the long-lived
**OAuth access token** (v1 writes), a 24 h **JWT** from `/users/api_token` (v2/auth reads), and the
**public API** (no auth) for community-ID polling.

---

## 2. Data model (`ww-backend`, declarative schema)

Authored under `supabase/schemas/` (run `DB_AGENT_MODE=1 npm run db:change inat_observations` to
generate the migration + types). Non-breaking: three new tables, no existing table touched.

| Table | Purpose | Access |
|---|---|---|
| `inat_tokens` | Fernet-encrypted per-user OAuth tokens | **service-role only** (no `authenticated` grant) |
| `inat_observations` | One row per published burst → iNat observation; holds `inat_observation_id/uuid/uri`, `species_guess`, `geoprivacy`, and the **`sync_status`** lifecycle + `quality_grade` + `community_taxon(_id)` | `SELECT` for project members (via deployment→project); writes service-role only |
| `inat_observation_media` | Join: WW photos per iNat observation (`inat_photo_id`, `original_filename`) — drives the thumbnail badge | same as above |

`sync_status` ∈ `pending · uploaded · needs_id · research · disagreement · failed`.

A **join table** (not a column on `media`) was chosen deliberately: it keeps the core,
mobile-synced `media` table untouched (non-breaking) and avoids a forward foreign key.

---

## 3. Account linking (OAuth2 + PKCE) — pre-existing

Already implemented before this work; surfaced in Phase 3.

- `services/inat_oauth.py`: `generate_pkce_pair` (S256), `build_authorization_url`,
  `exchange_code_for_token`, `refresh_access_token`, `get_api_jwt`, and **encrypted** token
  storage in `inat_tokens` (`encrypt_token`/`decrypt_token`, Fernet key derived from
  `INAT_CLIENT_SECRET`; refused, with the reason, when it is missing or shorter than 32 characters).
- Routes: `GET /api/inat/auth | /callback | /status`, `POST /api/inat/disconnect | /token`.
- UI: `INaturalistPanel.tsx` (connect/disconnect), surfaced on the **Toolkit** tab (the old *Other*
  tab was retired — `/other` now redirects to `/toolkit`, and the OAuth callback returns to
  `/toolkit?inat=connected`).

---

## 4. Publish (Phase 2)

`domain/inaturalist_publish.py → publish_media_to_inat(user_id, media_ids, gap_seconds, geoprivacy)`
exposed at **`POST /api/inat/publish`**.

1. **By-catch filter**: a photo goes up under the label its card shows
   (`public_api.photo_verdict`, the grid's `photoVerdict`), so a reviewer's Empty or person outvotes
   the AI. It stays off unless that label is an `animal`, and whenever any row, a model's or a
   person's, says `human` (`_photo_label`). Unprocessed media are dropped too.
2. **Idempotency** — skip media already in `inat_observation_media`.
3. **Burst consolidation** — cluster per deployment by temporal gap (`Δt < gap_seconds`, default 60 s);
   each burst → **one iNat observation with all its photos**. Untimed images each stand alone. A
   burst holding a photo the caller already published joins that photo's observation.
4. **species_guess**: the most common scientific name across the burst's photos, each photo by its
   label from step 1, so a cluster confirmed as one species and then reassigned goes up as the new one.
5. **Transactional upload** — `create_observation` (deployment lat/lon, earliest timestamp,
   `geoprivacy='obscured'` default) → insert `inat_observations` (`sync_status='uploaded'`) → for each
   photo, resolve bytes via `media_resolver` and **proxy-upload** through `upload_observation_photo`
   (bypassing browser CORS), with `original_filename = WW media id` → insert `inat_observation_media`.
   Photo failures flip the row to `failed` with an `error_message`.

Returns a summary: `observations_created`, `photos_uploaded`, `skipped_bycatch`,
`skipped_already_published`, `errors`, a per-observation list, and `remaining_media_ids`.

6. **Rounds** (#123): a request starts no photo after `PUBLISH_BUDGET_SECONDS` (120 s; the Container
   Apps ingress ends a request at about 240 s and one upload can take a minute) and hands back the
   photos it did not reach, with the published photos of a burst it stopped in, so step 3 puts the
   rest into the same observation. `useINat().publish` sends them again until none are left and
   answers once (`lib/inatPublishRounds.ts`). Chosen over a job (`enqueue_job`): with `REDIS_URL`
   set a job runs on the GPU worker, which would cold-start a T4 for uploads and need the
   iNaturalist secrets there, and the jobs dock is built for uploads. A tab closed between rounds
   leaves the rest unpublished; publishing the same selection again carries on.

> **Privacy / by-catch:** a photo showing a person, or labelled anything but an animal, is never
> uploaded; default geoprivacy is `obscured`.

---

## 5. Status badge (Phase 3)

`components/data/INatBadge.tsx` — a "dove" marker on the **top-right** of each thumbnail (the
annotation `StatusBadge` stays top-left). Colour by `inat_observations.sync_status`:

| State | Colour | Meaning |
|---|---|---|
| `uploaded` / `needs_id` | iNat green | on iNat, awaiting community ID |
| `research` | bright green | research grade — community confirmed ✓ |
| `disagreement` | amber | community taxon differs from your label |
| `failed` | red | upload failed |

`MediaBrowser` loads badge state with a **graceful** `inat_observation_media → inat_observations`
lookup (if the Phase-1 tables aren't migrated yet, it shows no badges rather than breaking). Clicking
the badge opens the iNat observation.

Selection + publish live in a new **"iNaturalist" ribbon tab**: Account (connect / ✓ username),
Selection (toggle + count), Publish (⬆ Upload N), Community IDs (↻ Sync). In select mode, clicking a
thumbnail toggles selection (green ✓) instead of opening the modal.

---

## 6. Sync-back (Phase 4)

`domain/inaturalist_sync.py → sync_inat_identifications(user_id=None, limit=200)` exposed at
**`POST /api/inat/sync`** (current user); the same function with no user filter is what a scheduled
job calls for everyone.

1. Load non-terminal `inat_observations`; **batch-poll the public iNat API** for `quality_grade`
   + `community_taxon` (reconciled via the stored mapping — no scraping / `original_filename` needed).
2. **Update the mapping** → `sync_status` (`_derive_status`: `research` if community confirms,
   `disagreement` if the community taxon differs from `species_guess`, else `needs_id`),
   `quality_grade`, `community_taxon`, `last_synced_at`. This flips the badge colour.
3. **Write back into WW** → a `source_type='consensus'` observation **per photo** in the burst
   (`classified_by='iNaturalist community'`, `review_status='consensus_approved'` at research grade),
   resolving `taxon_id` from the local `taxa` table. **Idempotent** (updates the existing consensus row
   rather than duplicating). Runs under the service role (RLS-bypassing; the schema grants it).

Returns `checked`, `updated`, `research`, `disagreement`, `observations_written`.

### What happens today (checked 25 Oct 2026, #123)

The round trip stops at the badge: no identification from iNaturalist reaches `observations`.

- **When it runs.** `InatAutoSync` (mounted in `App.tsx`) calls `POST /api/inat/sync` once per
  browser session for a signed-in user whose `/api/inat/status` says connected; Settings,
  Integrations, *Sync now* calls it on demand. Nothing is scheduled: no worker cron, nothing at API
  startup. Each call covers the caller's own observations only.
- **What it reads.** Up to 200 of the caller's rows in `uploaded`, `needs_id`, `research` or
  `disagreement`, in no order, so past 200 which ones are polled is arbitrary. A row marked `failed`
  (any of its photos failed to upload) is never polled again.
- **The community taxon is never read.** `batch_poll_observations` (and `get_observation_status`)
  read `community_taxon.name`, but `GET /v1/observations` has no `community_taxon` object, only
  `community_taxon_id` and `taxon` (checked against two public observations on the live API). So
  `community_taxon` is always null: `sync_status` follows `quality_grade` alone (`research`,
  `needs_id`, or `uploaded` for casual), which recolours the dove badge; `disagreement` never
  happens; `_write_consensus` never runs, and *Sync now* always reports 0 community IDs written.
- **Nothing goes the other way.** A label changed in Wildlife Watcher after publishing is not sent to
  iNaturalist; `species_guess` stays what it was at publish.

Fixing the read (`taxon.name` when `taxon.id == community_taxon_id`, else the taxon of
`community_taxon_id`) would switch on step 3 as written. Decide first what the rows should mean: at
research grade the row is `consensus_approved`, which `photo_verdict` and the pipeline's
`without_human_verdicts` count as a human verdict, so the card would show iNaturalist's taxon where
no person labelled the photo, and later pipeline runs would skip those photos.

---

## 7. File map

| Layer | File | Role |
|---|---|---|
| Schema | `ww-backend/supabase/schemas/public/tables/51..53_*.sql` + `yyy_policies/88,89_*.sql` | tables + RLS |
| Auth | `ww-website/backend/app/services/inat_oauth.py` | OAuth/PKCE + token storage (pre-existing) |
| Domain | `…/domain/inaturalist.py` | create/upload/poll helpers (pre-existing) |
| Domain | `…/domain/inaturalist_publish.py` | publish orchestrator (Phase 2) |
| Domain | `…/domain/inaturalist_sync.py` | sync-back (Phase 4) |
| API | `…/routers/inaturalist.py` | `/publish`, `/sync` (+ existing auth/observation routes) |
| Frontend | `…/hooks/useINat.ts` | status / connect / publish / sync |
| Frontend | `…/components/data/INatBadge.tsx` | dove badge |
| Frontend | `…/components/data/MediaBrowser.tsx` | selection, badges, ribbon tab |
| Frontend | `…/pages/ToolkitPage.tsx` | Connect iNaturalist card |

---

## 8. Go-live checklist

1. **Apply schema:** in `ww-backend`, `DB_AGENT_MODE=1 npm run db:change inat_observations`; review
   the generated migration; commit schema + generated artifacts; notify mobile via the type-sync guide.
2. **Configure:** `FF_INAT_ENABLED=true`, `INAT_CLIENT_ID`, `INAT_CLIENT_SECRET`, `INAT_REDIRECT_URI`
   in the website backend env.
3. **Register** the OAuth application on iNaturalist with the matching redirect URI.
4. **(Optional) automate sync:** schedule `sync_inat_identifications()` (no user filter) on the
   ARQ/job runner (e.g. hourly). Until then the **↻ Sync IDs** button covers it.

---

## 9. Open items / future work

- **Scheduled sync job** — currently manual (button) + endpoint; wire onto the job runner with an
  `updated_since` watermark for bandwidth efficiency.
- **Taxon registration on sync** — `_write_consensus` resolves `taxon_id` from local `taxa` only; if
  absent it stores `scientific_name` with a null `taxon_id`. Could auto-register via
  `search_and_fetch_inat_taxon` (already exists) when the community taxon isn't in `taxa`.
- **Human-scrubbing** — only *skips* by-catch today; could optionally blur/scrub human bounding boxes
  before upload (the context's MegaDetector approach) instead of dropping the frame.
- **v2 `original_filename` fallback** — we reconcile via the stored mapping; the authenticated v2 RISON
  `photos.original_filename` path is a redundant fallback if the mapping is ever lost.
- **pyinaturalist** — the hand-rolled `httpx` calls could adopt `pyinaturalist` for multipart/retry
  robustness.
