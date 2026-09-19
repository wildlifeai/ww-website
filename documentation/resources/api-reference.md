# Wildlife Watcher API Reference

Complete endpoint reference for the Wildlife Watcher V2 API.

**Base URL:**
`https://ww-backend.bravesand-8bd2f1d4.australiaeast.azurecontainerapps.io` (production) |
`https://ww-backend-dev.bravesand-8bd2f1d4.australiaeast.azurecontainerapps.io` (dev) |
`http://localhost:8000` (local)

> There is **no `api.wildlifewatcher.ai`** — the Cloudflare zone serves the frontend only. The API is
> reached at its Azure Container Apps FQDN; confirm the current values in
> [cloud-infrastructure](./cloud-infrastructure.md#azure).

**Authentication:** JWT Bearer token from Supabase Auth (required for protected endpoints).

**Response Format:** All endpoints return a standard envelope:

```json
{
  "data": { ... },
  "error": null,
  "meta": {
    "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "total": null,
    "page": null
  }
}
```

On error:

```json
{
  "data": null,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Human-readable description",
    "retryable": false,
    "details": "Optional diagnostic info"
  },
  "meta": { "request_id": "..." }
}
```

---

## Table of Contents

- [System](#system)
- [Auth](#auth)
- [Projects](#projects)
- [Jobs (Async)](#jobs-async)
- [Manifest Generation](#manifest-generation)
- [Model Conversion](#model-conversion)
- [EXIF Parsing](#exif-parsing)
- [LoRaWAN Webhooks](#lorawan-webhooks)
- [iNaturalist Integration](#inaturalist-integration)
- [Image Clustering](#image-clustering)
- [AI Pipeline](#ai-pipeline)
- [Media Registry](#media-registry)
- [Deployments](#deployments)
- [CamtrapDP Import](#camtrapdp-import)
- [Wildlife Brain — Embeddings & Clustering](#wildlife-brain--embeddings--clustering)
- [Conservation Intelligence](#conservation-intelligence)
- [QA](#qa)
- [Public Data API (v1)](#public-data-api-v1)
- [Error Codes](#error-codes)

---

> Every endpoint returns the standard `ApiResponse` envelope — `{ "data": …, "meta": { "request_id": … } }`
> on success, or `{ "error": { "code", "message" }, "meta": … }` on failure. The authoritative
> request/response schemas are always at **`/docs`** (Swagger) / **`/openapi.json`**.

## System

| Method · Path | Description |
|---|---|
| `GET /health` | Health probe → `{ "status": "ok" }` (no auth) |
| `GET /docs` · `GET /redoc` | Interactive Swagger / ReDoc docs |
| `GET /openapi.json` | Raw OpenAPI 3.0 spec |

---

## Auth

Prefix `/api/auth`. Everything else authenticates with a Supabase JWT obtained in the browser; this
router exists only to mint the shared read-only demo session server-side. Detail:
[demo-account](./demo-account.md).

| Method · Path | Auth | Description |
|---|---|---|
| `POST /api/auth/demo-session` | None (rate-limited 10/min per IP) | Mint a session for the shared demo account → `{ access_token, refresh_token }`. Returns `DEMO_DISABLED` when `DEMO_EMAIL`/`DEMO_PASSWORD` are unset on the server |

---

## Projects

Prefix `/api/projects`. JWT required. **Reads go direct to Supabase** under RLS
(`supabase.from('projects')`) — there is no `GET /api/projects`; this router covers only the writes
that need service-role cascades or admin checks.

| Method · Path | Auth | Description |
|---|---|---|
| `POST /api/projects` | JWT | Create a project in the caller's organisation — body `{ name, description? }` |
| `DELETE /api/projects/{project_id}` | JWT · `project_admin` | Soft-delete, cascading to deployments → media → observations. Returns the shared `deleted_at` so the client can offer Undo. Members/viewers get `404` |
| `POST /api/projects/{project_id}/restore` | JWT · `project_admin` | Undo a soft-delete using that `deleted_at` |

---

## Jobs (Async)

Long-running operations (manifest, model conversion, pipeline, embedding) return a `job_id`
immediately; poll these to track progress. Job IDs are unguessable UUIDs (no auth). Prefix `/api/jobs`.

| Method · Path | Description |
|---|---|
| `GET /api/jobs/{job_id}` | Status + `progress`, `current_phase`, ordered `events[]`, `summary` |
| `GET /api/jobs/{job_id}/result` | Result of a completed job (`result_url`); `409` if not yet complete |

**Status values:** `queued` → `processing` → `completed` · `completed_with_errors` (some items failed —
check `summary`/logs) · `failed` (`error` carries the reason).

---

## Manifest Generation

Build a camera `MANIFEST.zip` firmware package. Async (returns a `job_id`). No auth. Prefix `/api/manifest`.

| Method · Path | Description |
|---|---|
| `POST /api/manifest/generate` | Body `{ model_source, model_type?, resolution?, sscma_model_id?, org_model_id?, camera_type? }` → `{ job_id, status }` |

- **`model_source`:** `default` (best in DB) · `github` (+ `model_type`, `resolution`) · `sscma` (+ `sscma_model_id`) · `organisation` (+ `org_model_id`).
- **GitHub models:** Person Detection `96x96`. The YOLOv8/YOLOv11 detection and pose entries are registered but blocked (the device can only run classifiers; see [AI Model Pipeline](./ai-model-pipeline.md#pre-trained-model-github-zoo)), and `GET /api/models/pretrained/catalog` omits them.
- **`camera_type`:** `Raspberry Pi` (default) · `HM0360`.

> **Async pattern** (all job-returning endpoints): `POST …` → `{ job_id }`, then poll
> `GET /api/jobs/{job_id}` until `completed`, then download from its `result_url`.

---

## Model Conversion

Edge Impulse → Vela (Ethos-U55) conversion + registration. Async. `multipart/form-data` for uploads.
Architecture: [AI Model Pipeline](./ai-model-pipeline.md). Prefix `/api/models`.

| Method · Path | Auth | Description |
|---|---|---|
| `POST /api/models/convert` | JWT · org-manager | Upload + convert a model file (`.zip`/`.tflite`/`.cc`, ≤50 MB) — form `file`, `model_name`, `description?`, `organisation_id?` → `{ job_id, model_id }` |
| `POST /api/models/pretrained` | JWT · org-manager | Download + register a zoo model — body `{ source_type: "pretrained"\|"sscma", architecture?, resolution?, sscma_uuid?, model_name?, … }` |
| `GET /api/models/pretrained/catalog` | None | Built-in pretrained registry (architectures, resolutions, labels) |
| `GET /api/models/sscma/catalog` | None | SSCMA model catalog (cached 1 h) |
| `GET /api/models/managed-orgs` | JWT | Orgs where the user is `organisation_manager` |
| `GET /api/models/train/status` | JWT | Whether training is on (`FF_MODEL_TRAINING_ENABLED`), the trainer `mode` (`edge_impulse` \| `export_only`) and the dataset limits the UI validates against |
| `POST /api/models/train` | JWT (verified) · org-manager, 10/min | Train a Species Brain from an Annotations selection. Body `{ media_ids, model_name, classes: [{ label, scientific_name, … }], include_background?, background_label?, image_size? (96\|160), colour?, epochs?, organisation_id? }` → `{ job_id, model_id, mode, poll_url }`. Every image must be in a deployment the caller can read. See [species-brain-training-spec](../development%20reports/species-brain-training-spec.md) |

---

## EXIF Parsing

Parse EXIF from uploaded JPEGs (sync). No auth. `multipart/form-data`. Prefix `/api/exif`. Drives the
image-upload pipeline — see [03-DATA-AND-SYNC](../onboarding/03-DATA-AND-SYNC.md).

| Method · Path | Description |
|---|---|
| `POST /api/exif/parse` | Form `files[]` (+ optional `paths[]`, `upload_to_drive`, `assigned_deployment_id`, `run_ai`) → per-file parsed EXIF; buffers bytes to Azure + enqueues the Drive upload job. `assigned_deployment_id` binds photos that carry no valid deployment ID; `run_ai` (default `true`) gates the post-upload AI pipeline + Wildlife Brain. Response `drive_upload` tells you whether anything was actually stored — clients **must** treat `{enabled: false}` (`reason: server_disabled` = `GOOGLE_DRIVE_ENABLED` unset, or `not_requested`) and `{status: "skipped", reason: "no_deployment_id"}` as *nothing saved* (media rows are only created by the Drive job); `{status: "error"}` = not authenticated; otherwise it carries the enqueued `job_id` |

**Key extracted fields:** `deployment_id` (firmware tag `0xF200` → `UserComment` → `Custom_Data`),
`latitude`/`longitude` (GPS DMS→decimal), `date` (Original → Create → DateTime), `Make`/`Model`,
and `temperature_c`/`battery_pct` (parsed from `UserComment` telemetry). The full set lands in
`media.exif_metadata`.

---

## LoRaWAN Webhooks

Receive device uplinks from LoRaWAN network servers. Webhooks authenticate with the
**`X-Webhook-Secret`** header (`LORAWAN_TTN_WEBHOOK_SECRET` / `LORAWAN_CHIRPSTACK_WEBHOOK_SECRET` /
`LORAWAN_WEBHOOK_SECRET`); query endpoints use JWT. Gated by `FF_LORAWAN_WEBHOOKS_ENABLED`. Prefix
`/api/lorawan`. Payload formats + network-server config: [LoRaWAN Webhook Setup](./lorawan-webhook-setup.md).

| Method · Path | Auth | Description |
|---|---|---|
| `POST /api/lorawan/webhook/ttn` | `X-Webhook-Secret` | TTN v3 uplink → parsed battery / SD-card / model-output |
| `POST /api/lorawan/webhook/chirpstack` | `X-Webhook-Secret` | Chirpstack v4 uplink |
| `GET /api/lorawan/messages` | JWT | Org-scoped parsed-message list |
| `GET /api/lorawan/messages/{device_eui}/latest` | JWT | Latest parsed message for a device EUI |

---

## iNaturalist Integration

OAuth connect + observation publish/poll. Gated by `FF_INAT_ENABLED` (404 when off). JWT unless noted.
Prefix `/api/inat`. The Annotations bulk "Upload to iNaturalist" / "Sync IDs" flows build on these —
see [05-ANNOTATION-WORKFLOW](../onboarding/05-ANNOTATION-WORKFLOW.md).

| Method · Path | Auth | Description |
|---|---|---|
| `GET /api/inat/auth` | JWT | Start OAuth → `{ authorization_url, state }` |
| `GET /api/inat/callback` | None (state) | OAuth redirect handler → stores tokens, 302 to `/toolkit?inat=connected` |
| `POST /api/inat/token` | JWT | Exchange an authorization code for tokens (the non-redirect path) |
| `GET /api/inat/status` | JWT | Connection status (`connected`, `inat_username`, …) |
| `POST /api/inat/disconnect` | JWT | Revoke stored tokens |
| `POST /api/inat/observations` | JWT | Create an observation — body `{ species_guess, latitude, longitude, observed_on, geoprivacy?, … }` |
| `GET /api/inat/observations/{observation_id}/status` | None | Identification status for one observation |
| `POST /api/inat/observations/poll` | JWT | Batch status for ≤200 observation ids (`{ observation_ids: [...] }`) |
| `POST /api/inat/publish` | JWT | **Publish WW media to iNat** — consolidates bursts by temporal gap, drops human/vehicle/blank by-catch, proxy-uploads photos. Body `{ media_ids, gap_seconds?, geoprivacy? }` (default `obscured`) → `observations_created`, `photos_uploaded`, `skipped_*`, `errors` |
| `POST /api/inat/sync` | JWT | **Pull community IDs back** — batch-polls `quality_grade` + `community_taxon`, updates `sync_status`, writes `source_type='consensus'` observations into WW. Idempotent → `checked`, `updated`, `research`, `disagreement`, `observations_written` |
| `GET /api/inat/taxa/search` | JWT | iNat taxa autocomplete — backs the `SpeciesPicker` alongside the local `taxa` table |
| `POST /api/inat/taxa` | JWT | Register an iNat taxon + its lineage locally, returning a real `taxon_id` |

> Publish/sync design detail: [inaturalist-integration](../development%20reports/inaturalist-integration.md).
> The picker flow is in [05-ANNOTATION-WORKFLOW](../onboarding/05-ANNOTATION-WORKFLOW.md#speciespicker-taxon-validation).

---

## Image Clustering

Near-duplicate detection via perceptual hashing (dHash + BK-tree). No auth. `multipart/form-data`.
≤1000 images/request. Prefix `/api/clustering`.

> **Legacy** — this is pixel near-duplicate grouping. For semantic DINOv3 clustering (what the
> Annotations *Group by → Cluster* uses), see [Wildlife Brain](#wildlife-brain--embeddings--clustering).

| Method · Path | Description |
|---|---|
| `POST /api/clustering/analyze` | Form `files[]`, `max_hamming?` (0–20, default 10) → clusters + representatives |
| `POST /api/clustering/analyze/csv` | Same logic; returns a `text/csv` download |

---

## AI Pipeline

Run inference + ecological event/effort computation on a deployment. Gated by `FF_PIPELINE_ENABLED`
(404 when off). JWT required. Prefix `/api/pipeline`. Architecture:
[04-AI-PIPELINE](../onboarding/04-AI-PIPELINE.md).

| Method · Path | Description |
|---|---|
| `POST /api/pipeline/run` | Run the pipeline — body `{ deployment_id, steps?, confidence_threshold?, config?, only_unannotated? }`. Steps: `media_prep`, `speciesnet`, `animal_crop`, `bioclip`. Returns per-step + aggregate counts and records an `annotation_run` |
| `POST /api/pipeline/events/cluster` | Group observations into ecological events by temporal gap — body `{ deployment_id, gap_minutes?, min_images? }` |
| `POST /api/pipeline/effort/{deployment_id}` | Compute + store effort (trap-nights, uptime, false-trigger rate) |
| `GET /api/pipeline/effort/{deployment_id}` | Retrieve cached effort stats |

> The pipeline also runs **automatically after upload** (`auto_annotate_deployments`) — the endpoints
> above are the manual trigger. Step set + auto-run details: [04-AI-PIPELINE](../onboarding/04-AI-PIPELINE.md).

---

## Media Registry

Image rendition resolution, thumbnails/crops, and bulk media operations. JWT required; the rendition
endpoints are gated by `FF_MEDIA_REGISTRY_ENABLED`. All return the standard `ApiResponse` envelope.

| Method · Path | Description |
|---|---|
| `GET /api/media/{media_id}/image` | Serve/proxy a media image (`?size=thumb\|full`); resolves public files / signed URLs |
| `GET /api/media/{media_id}/resolve` | Resolve a media id to a displayable URL (rendition or signed original) |
| `GET /api/media/registry/{deployment_id}` | Rendition status for a deployment's media |
| `POST /api/media/thumbnails/{deployment_id}` | Enqueue a thumbnail/preview backfill (async job) |
| `DELETE /api/media/batch` | Soft-delete media by id list — body `{ "media_ids": [...] }` |
| `POST /api/media/run-selected` | Run the AI pipeline on a media subset — body `{ "media_ids": [...], "steps": [...] }` |

---

## Deployments

Deployment helpers used by the upload flow. JWT required. Prefix `/api/deployments`.

| Method · Path | Description |
|---|---|
| `POST /api/deployments` | Create a deployment (+ placeholder device) in a project you can access — body `{ project_id, name?, id?, location_name?, latitude?, longitude?, deployment_start?, deployment_end? }`. Backs the "assign/create a deployment at upload" flow: pass the new id as `assigned_deployment_id` to `/api/exif/parse` to bind photos that carry no valid deployment ID. `id` (a UUID) creates the row under the id the camera stamped into the photos' EXIF, so the phone that configured the camera converges on it when it syncs; `400` if not a UUID, `409` if it already exists |
| `POST /api/deployments/validate` | Resolve deployment ids to `valid` / `no_access` / `not_found` — the upload pre-check that drives the warning banners. Accepts full UUIDs (from EXIF `0xF200`) and 8-hex card-folder prefixes. Body `{ "deployment_ids": ["e10f7c43-…", "7785FABB", …] }` |
| `POST /api/deployments/backfill-timezones` | Derive `deployments.timezone` from GPS for rows missing it (idempotent) |

---

## CamtrapDP Import

Prefix `/api/camtrapdp`. Gated by `FF_CAMTRAPDP_IMPORT_ENABLED`.

| Method · Path | Description |
|---|---|
| `POST /api/camtrapdp/import` | Import a CamtrapDP `.zip` — multipart `file`, `annotation_mode` (default `final`), `run_ai` (default `false`) → creates deployments + media + observations. `annotation_mode=final` treats the package as a finished dataset (provenance mapped from `classificationMethod`; media with no observation get a reviewed `blank`); `unprocessed` leaves unlabelled media bare as work to do. `run_ai=true` additionally runs SpeciesNet + Wildlife Brain on the image-backed imported deployments → returns `ai_job_id` |

> Public-API export of CamtrapDP is `POST /api/v1/export/camtrapdp` (see [Public Data API](#public-data-api-v1)).

---

## Wildlife Brain — Embeddings & Clustering

DINOv3 embeddings → UMAP → HDBSCAN clustering → vector-store similarity, and the active-learning review
queue. JWT required; gated by **`FF_WILDLIFE_BRAIN_ENABLED`** (returns `FEATURE_DISABLED` when off).
Prefix `/api/brain`. Architecture: [04-AI-PIPELINE](../onboarding/04-AI-PIPELINE.md).

> **Vector store:** these endpoints are backed by **`pgvector` in Supabase** (live since 2026-07-09) —
> vectors live in `media_embeddings.embedding`, searched via the `match_media_embeddings` RPC. The former
> **Qdrant** container has been **removed**; see
> [deployment guide → Vector Store](deployment-guide.md#vector-store--pgvector-supabase).
> `/api/brain/backup` is now a **no-op** (retained for compatibility): pgvector inherits Supabase PITR, so
> there is no separate snapshot to take.

> Distinct from [Image Clustering](#image-clustering) (`/api/clustering`), which is the legacy
> perceptual-hash near-duplicate grouping. `/api/brain` is the semantic DINOv3 clustering the
> Annotations grid's *Group by → Cluster* uses.

| Method · Path | Description |
|---|---|
| `POST /api/brain/embed/{deployment_id}` | Embed + cluster a deployment (server mode enqueues a GPU job) |
| `GET /api/brain/clusters/{deployment_id}` | Clusters from the deployment's latest embedding run |
| `POST /api/brain/clusters/multi` | Aggregate clusters across deployments — body `{ "deployment_ids": [...], "min_confidence": 0 }`; returns `clusters`, `media_clusters` (media→cluster map), `outlier_media_ids` |
| `GET /api/brain/umap/{deployment_id}` | Persisted 2-D UMAP scatter coordinates |
| `GET /api/brain/outliers/{deployment_id}` | HDBSCAN-rejected images (expert-review candidates) |
| `GET /api/brain/similar/{media_id}` | Vector-store nearest-neighbour search (`?n=20&org_scoped=true`) |
| `POST /api/brain/clusters/{cluster_assignment_id}/confirm` | Confirm a cluster as a taxon → bulk-creates human observations for its members |
| `GET /api/brain/embedding-runs/{deployment_id}` | List embedding runs (model version, status, image count) |
| `POST /api/brain/reprocess/deployment/{deployment_id}` | Supersede current runs and re-embed a deployment |
| `POST /api/brain/reprocess/project/{project_id}` | Reprocess every deployment in a project |
| `POST /api/brain/reprocess/all` | Platform-wide re-embed — default dry-run returns a cost estimate; `confirm=true` executes |
| `GET /api/brain/compare-runs` | Compare cluster assignments between two runs (`?run_a=&run_b=`) |
| `POST /api/brain/backup` | **No-op** (compat shim) — pgvector inherits Supabase PITR, so there's no separate snapshot; the job completes with nothing to do |

### Active Learning & Review

Same prefix; the queue/score endpoints are additionally gated by **`FF_ACTIVE_LEARNING_ENABLED`**.

| Method · Path | Description |
|---|---|
| `POST /api/brain/recalculate-al-scores/{deployment_id}` | Recompute active-learning scores for a deployment |
| `GET /api/brain/review-queue/{deployment_id}` | Media ranked by `active_learning_score` DESC with AI label + score reasons (`?limit=50`) |
| `POST /api/brain/review/{media_id}` | Record a reviewer decision (`approve` / `reassign` / `expert`) → human observation |

---

## Conservation Intelligence

Dataset health, alerts, and ecological summaries. JWT required; gated by **`FF_INTELLIGENCE_ENABLED`**.
Prefix `/api/intelligence`.

| Method · Path | Description |
|---|---|
| `POST /api/intelligence/shift-detection/{deployment_id}` | Detect distribution shift between two time windows |
| `GET /api/intelligence/health/{project_id}` | Dataset health: species coverage, review funnel, outlier rate |
| `GET /api/intelligence/alerts/{project_id}` | Active (unacknowledged) conservation alerts |
| `GET /api/intelligence/unknown-species/{org_id}` | Provisional taxa awaiting expert confirmation |
| `GET /api/intelligence/occupancy/{project_id}` | Species-assemblage overlap (Jaccard) between deployments |
| `GET /api/intelligence/accumulation/{deployment_id}` | Species accumulation curve over time |

---

## QA

Prefix `/api/qa`. JWT required.

| Method · Path | Description |
|---|---|
| `GET /api/qa/report/{deployment_id}` | AI-vs-human agreement (a precision proxy over images carrying both an AI and a human label) |

---

## Public Data API (v1)

Token-authenticated **read** API for external integrations. Data endpoints authenticate with an
**`X-API-Key`** header (not the JWT) carrying a `<resource>:read` scope; the key-management endpoints
use the normal JWT. Gated by **`FF_PUBLIC_API_ENABLED`**. Prefix `/api/v1`.

| Method · Path | Auth | Description |
|---|---|---|
| `POST /api/v1/api-keys` | JWT | Create an API key (the secret is returned **once**) |
| `GET /api/v1/api-keys` | JWT | List your API keys (metadata only, no secrets) |
| `DELETE /api/v1/api-keys/{key_id}` | JWT | Revoke an API key |
| `GET /api/v1/deployments` | `X-API-Key` · `deployments:read` | List deployments (filter `?project_id=&status=&limit=&offset=`) |
| `GET /api/v1/deployments/{deployment_id}` | `X-API-Key` · `deployments:read` | Deployment detail |
| `GET /api/v1/devices` | `X-API-Key` · `devices:read` | List devices |
| `GET /api/v1/devices/{device_eui}/telemetry` | `X-API-Key` · `telemetry:read` | Device LoRaWAN telemetry |
| `GET /api/v1/observations` | `X-API-Key` · `observations:read` | List observations (filterable) |
| `POST /api/v1/export/camtrapdp` | `X-API-Key` · `export:camtrapdp` | Export a CamtrapDP package |

---

## Error Codes


| HTTP Code | Meaning | Retryable |
|-----------|---------|-----------|
| 400 | Bad request (invalid input) | No |
| 401 | Unauthorized (invalid/missing JWT or webhook secret) | No |
| 404 | Resource not found | No |
| 409 | Conflict (e.g., job not yet completed) | Yes |
| 413 | Payload too large | No |
| 422 | Validation error (invalid request body) | No |
| 429 | Rate limit exceeded | Yes (after backoff) |
| 500 | Internal server error | Yes |

### Rate Limiting

The API enforces per-IP rate limits (default: 60 requests/minute). When exceeded:

```json
{
  "error": "Rate limit exceeded: 60 per 1 minute"
}
```

Include `Retry-After` header in your retry logic.

### Request Tracing

Every response includes an `X-Request-ID` header and `meta.request_id` field. Include this in bug reports for log correlation.
