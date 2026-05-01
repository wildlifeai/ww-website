# Wildlife Watcher API Reference

Complete endpoint reference for the Wildlife Watcher V2 API.

**Base URL:** `https://api.wildlifewatcher.ai` (production) | `http://localhost:8000` (local)

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
- [Jobs (Async)](#jobs-async)
- [Manifest Generation](#manifest-generation)
- [Model Conversion](#model-conversion)
- [EXIF Parsing](#exif-parsing)
- [LoRaWAN Webhooks](#lorawan-webhooks)
- [iNaturalist Integration](#inaturalist-integration)
- [Image Clustering](#image-clustering)
- [Error Codes](#error-codes)

---

## System

### `GET /health`

Health probe for Docker/Render/load balancer checks.

**Authentication:** None

**Response:**

```json
{ "status": "ok" }
```

**Status Codes:**

| Code | Meaning |
|------|---------|
| 200 | Service is healthy |

---

### `GET /docs`

Interactive Swagger UI documentation.

### `GET /redoc`

ReDoc-formatted API documentation.

### `GET /openapi.json`

Raw OpenAPI 3.0 specification.

---

## Jobs (Async)

Long-running operations (manifest generation, model conversion) return a `job_id` immediately. Poll these endpoints to track progress.

### `GET /api/jobs/{job_id}`

Get the current status of an async job.

**Authentication:** None (job IDs are unguessable UUIDs)

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `job_id` | string | UUID returned when the job was created |

**Response (200):**

```json
{
  "data": {
    "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "status": "processing",
    "progress": 0.5,
    "created_at": "2026-04-12T03:00:00Z",
    "updated_at": "2026-04-12T03:00:05Z",
    "result_url": null,
    "error": null,
    "message": "📥 Downloading images from Supabase...",
    "current_phase": "download",
    "summary": {
      "total": 38,
      "downloaded": 10,
      "uploaded": 0,
      "skipped": 0,
      "failed": 0,
      "current_phase": "download",
      "started_at": "2026-04-12T03:00:02Z"
    },
    "events": [
      {
        "type": "progress",
        "phase": "download",
        "timestamp": "2026-04-12T03:00:05Z",
        "message": "Downloaded image 10/38 from Supabase ✓",
        "seq": 10
      }
    ]
  },
  "meta": { "request_id": "..." }
}
```

**Job Status Values:**

| Status | Description |
|--------|-------------|
| `queued` | Job created, waiting for worker to pick up |
| `processing` | Worker is actively processing the job |
| `completed` | Done without errors |
| `completed_with_errors` | Done, but some items (e.g. specific files) failed. Check summary/logs. |
| `failed` | Complete failure — `error` field has the failure reason |

**Status Codes:**

| Code | Meaning |
|------|---------|
| 200 | Job found, status returned |
| 404 | Job ID not found (expired or invalid) |

---

### `GET /api/jobs/{job_id}/result`

Get the result of a completed job.

**Authentication:** None

**Response (200):**

```json
{
  "data": {
    "result_url": "https://xxx.supabase.co/storage/v1/object/sign/firmware/temp/..."
  },
  "meta": { "request_id": "..." }
}
```

**Status Codes:**

| Code | Meaning |
|------|---------|
| 200 | Result available |
| 404 | Job not found, or result expired |
| 409 | Job is not yet completed |

---

## Manifest Generation

### `POST /api/manifest/generate`

Generate a MANIFEST.zip firmware package for camera SD card deployment. This is an **async** operation — returns a `job_id` for polling.

**Authentication:** None (public endpoint for firmware downloads)

**Request Body:**

```json
{
  "model_source": "github",
  "model_type": "YOLOv11 Object Detection",
  "resolution": "192x192",
  "camera_type": "Raspberry Pi"
}
```

**Parameters:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `model_source` | string | No | `"default"` | Model source type (see below) |
| `model_type` | string | Conditional | — | Model name (for `github` source) |
| `resolution` | string | Conditional | — | Resolution (for `github` source) |
| `sscma_model_id` | string | Conditional | — | SSCMA model ID (for `sscma` source) |
| `org_model_id` | string | Conditional | — | Supabase ai_models.id (for `organisation` source) |
| `camera_type` | string | No | `"Raspberry Pi"` | Camera hardware configuration |

**Model Source Options:**

| Source | Description | Required Fields |
|--------|-------------|-----------------|
| `default` | Best available model from the database | _(none)_ |
| `github` | Pre-trained model from GitHub model zoo | `model_type`, `resolution` |
| `sscma` | Model from SSCMA catalog | `sscma_model_id` |
| `organisation` | Custom model from your org's uploads | `org_model_id` |

**Available GitHub Models:**

| Model | Resolutions |
|-------|-------------|
| `Person Detection` | `96x96` |
| `YOLOv8 Object Detection` | `192x192` |
| `YOLOv11 Object Detection` | `192x192`, `224x224` |
| `YOLOv8 Pose Estimation` | `256x256` |

**Camera Types:**

| Type | Description |
|------|-------------|
| `Raspberry Pi` | Standard RPi camera (v1/v2/v3) |
| `HM0360` | Himax HM0360 motion sensor |

**Response (200):**

```json
{
  "data": {
    "job_id": "a1b2c3d4-...",
    "status": "queued"
  },
  "meta": { "request_id": "..." }
}
```

**Example — Download Workflow:**

```bash
# 1. Submit generation request
curl -X POST http://localhost:8000/api/manifest/generate \
  -H "Content-Type: application/json" \
  -d '{"model_source": "github", "model_type": "YOLOv11 Object Detection", "resolution": "192x192"}'
# → {"data": {"job_id": "abc-123", "status": "queued"}, ...}

# 2. Poll for completion
curl http://localhost:8000/api/jobs/abc-123
# → {"data": {"status": "processing", "progress": 0.5, ...}, ...}

# 3. Get result
curl http://localhost:8000/api/jobs/abc-123
# → {"data": {"status": "completed", "result_url": "https://...", ...}, ...}

# 4. Download MANIFEST.zip from result_url
curl -o MANIFEST.zip "https://xxx.supabase.co/storage/v1/..."
```

---

## Model Conversion

> See [AI Model Pipeline](./ai-model-pipeline.md) for full architecture documentation.

### `POST /api/models/convert`

Upload and convert a model file for Ethos-U55 deployment. This is an **async** operation — returns a `job_id` for polling.

**Authentication:** Required (JWT Bearer token). User must have `organisation_manager` role.

**Content-Type:** `multipart/form-data`

**Parameters:**

| Field | Type | Max Size | Description |
|-------|------|----------|-------------|
| `file` | binary | 50 MB | Model file (`.zip`, `.tflite`, or `.cc`) |
| `model_name` | string | — | Display name for the model (used for family grouping and versioning) |
| `description` | string | — | Optional description |
| `organisation_id` | string | — | Optional org UUID (auto-resolved if user manages only one) |

**Response (200):**

```json
{
  "data": {
    "job_id": "def-456",
    "model_id": "abc-789",
    "status": "queued"
  },
  "meta": { "request_id": "..." }
}
```

**Status Codes:**

| Code | Meaning |
|------|---------|
| 200 | Job enqueued successfully |
| 400 | Invalid file type or corrupt file |
| 401 | Not authenticated |
| 403 | User is not an organisation manager |
| 413 | File exceeds 50 MB limit |

---

### `POST /api/models/pretrained`

Download, package, and register a pre-trained model. Supports both GitHub Zoo and SSCMA sources. This is an **async** operation.

**Authentication:** Required (JWT Bearer token). User must have `organisation_manager` role.

**Request Body:**

```json
{
  "source_type": "pretrained",
  "architecture": "Person Detection",
  "resolution": "96x96",
  "description": "Optional description",
  "organisation_id": "b0000000-..."
}
```

**Parameters:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source_type` | string | Yes | `"pretrained"` (GitHub Zoo) or `"sscma"` (SenseCap Zoo) |
| `architecture` | string | For `pretrained` | Model architecture name |
| `resolution` | string | For `pretrained` | Input resolution (e.g. "96x96") |
| `sscma_uuid` | string | For `sscma` | UUID from the SSCMA catalog |
| `model_name` | string | For `sscma` | Display name |
| `description` | string | No | Optional description |
| `organisation_id` | string | No | Org UUID (auto-resolved if only one) |

**Response (200):**

```json
{
  "data": {
    "job_id": "ghi-012",
    "status": "queued"
  },
  "meta": { "request_id": "..." }
}
```

---

### `GET /api/models/pretrained/catalog`

Return the built-in pretrained model registry. Used by the frontend to dynamically render architecture/resolution dropdowns.

**Authentication:** None

**Response (200):**

```json
{
  "data": [
    {
      "architecture": "Person Detection",
      "firmware_model_id": 20,
      "resolutions": ["96x96"],
      "labels": ["no person", "person"]
    },
    {
      "architecture": "YOLOv11 Object Detection",
      "firmware_model_id": 1,
      "resolutions": ["192x192", "224x224"],
      "labels": ["object"]
    }
  ],
  "meta": { "request_id": "..." }
}
```

---

### `GET /api/models/sscma/catalog`

Get the SSCMA (Seeed Studio Model Assistant) model catalog. Results are cached for 1 hour.

**Authentication:** None

**Response (200):**

```json
{
  "data": [
    {
      "name": "YOLOv8n Detection",
      "uuid": "...",
      "description": "...",
      "task": "detection"
    }
  ],
  "meta": { "request_id": "..." }
}
```

---

### `GET /api/models/managed-orgs`

List organisations where the current user has the `organisation_manager` role. Used by the Upload Model form to populate the organisation selector.

**Authentication:** Required (JWT Bearer token)

**Response (200):**

```json
{
  "data": [
    { "id": "b0000000-...", "name": "Wildlife AI" }
  ],
  "meta": { "request_id": "..." }
}
```

---

## EXIF Parsing

### `POST /api/exif/parse`

Parse EXIF metadata from uploaded JPEG images. Extracts standard fields (DateTime, GPS) and custom Wildlife Watcher firmware tags (deployment ID). This is a **sync** (immediate response) operation.

**Authentication:** None

**Content-Type:** `multipart/form-data`

**Parameters:**

| Field | Type | Description |
|-------|------|-------------|
| `files` | binary[] | One or more JPEG files |

**Response (200):**

```json
{
  "data": [
    {
      "filename": "IMG_0001.jpg",
      "exif": {
        "DateTime": "2026:04:12 10:30:00",
        "Datetime_Original": "2026:04:12 10:30:00",
        "latitude": -36.848461,
        "longitude": 174.763336,
        "GPS_Altitude": 42.5,
        "deployment_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "date": "2026:04:12 10:30:00"
      }
    }
  ],
  "meta": { "request_id": "..." }
}
```

**Extracted Fields:**

| Field | Source | Description |
|-------|--------|-------------|
| `DateTime` | EXIF tag 0x0132 | Camera date/time |
| `Datetime_Original` | EXIF tag 0x9003 | Original capture time |
| `latitude` | GPS tags | Decimal degrees (computed from DMS) |
| `longitude` | GPS tags | Decimal degrees |
| `GPS_Altitude` | GPS tag 0x0006 | Altitude in meters |
| `deployment_id` | Custom tags | UUID extracted from firmware EXIF (tag 0xF200, UserComment, or Custom_Data) |
| `date` | Normalised | First available: Original > Create > DateTime |

**Example:**

```bash
curl -X POST http://localhost:8000/api/exif/parse \
  -F "files=@IMG_0001.jpg" \
  -F "files=@IMG_0002.jpg"
```

---

## LoRaWAN Webhooks

Endpoints for receiving real-time device uplinks from LoRaWAN network servers.

### `POST /api/lorawan/webhook/ttn`

Receive a TTN (The Things Network) v3 uplink webhook.

**Authentication:** `X-Webhook-Secret` header (must match `LORAWAN_TTN_WEBHOOK_SECRET` or `LORAWAN_WEBHOOK_SECRET` env var)

**Request Body:** TTN v3 uplink format:

```json
{
  "end_device_ids": {
    "device_id": "ww-camera-01",
    "dev_eui": "0004A30B001F9ACB",
    "application_ids": {
      "application_id": "wildlife-watcher"
    }
  },
  "uplink_message": {
    "frm_payload": "S0o=",
    "f_port": 1,
    "rx_metadata": [...]
  },
  "received_at": "2026-04-12T03:00:00Z"
}
```

**Response (200):**

```json
{
  "data": {
    "device_eui": "0004A30B001F9ACB",
    "battery_level": 75.0,
    "sd_card_used_capacity": 42.0,
    "model_output": { "detection": "person", "confidence": 0.95 },
    "raw_payload_hex": "4b2a7b226465746563...",
    "received_at": "2026-04-12T03:00:00Z"
  },
  "meta": { "request_id": "..." }
}
```

**Status Codes:**

| Code | Meaning |
|------|---------|
| 200 | Uplink processed and stored |
| 401 | Invalid webhook secret |
| 422 | Invalid payload format |

---

### `POST /api/lorawan/webhook/chirpstack`

Receive a Chirpstack v4 uplink webhook.

**Authentication:** `X-Webhook-Secret` header

**Request Body:** Chirpstack v4 HTTP integration format:

```json
{
  "deviceInfo": {
    "devEui": "0004A30B001F9ACB",
    "deviceName": "ww-camera-01",
    "applicationId": "app-123"
  },
  "data": "S0o=",
  "fPort": 1,
  "time": "2026-04-12T03:00:00Z"
}
```

---

### `GET /api/lorawan/messages`

List LoRaWAN messages for the authenticated user's organisation.

**Authentication:** Required (JWT Bearer token)

**Response (200):**

```json
{
  "data": [],
  "meta": { "request_id": "..." }
}
```

> Note: Currently returns empty array. Full implementation with RLS-scoped queries coming in Phase 2.

---

### `GET /api/lorawan/messages/{device_eui}/latest`

Get the latest parsed message for a specific device.

**Authentication:** Required (JWT Bearer token)

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `device_eui` | string | LoRaWAN Device EUI (16 hex chars) |

---

## iNaturalist Integration

> Gated behind the `FF_INAT_ENABLED` feature flag. All endpoints return 404 when disabled.

### `GET /api/inat/auth`

Start the iNaturalist OAuth flow. Returns an authorization URL for redirecting the user.

**Authentication:** Required (JWT Bearer token)

**Response (200):**

```json
{
  "data": {
    "authorization_url": "https://www.inaturalist.org/oauth/authorize?client_id=...&state=...",
    "state": "abc123"
  },
  "meta": { "request_id": "..." }
}
```

---

### `GET /api/inat/callback`

Handle the OAuth redirect from iNaturalist. Exchanges the authorization code for tokens, stores them encrypted in Supabase, and redirects the user back to the frontend.

**Authentication:** None (state param validates the request)

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `code` | string | Authorization code from iNaturalist |
| `state` | string | CSRF state token (must match the one from `/auth`) |

**Response:** 302 redirect to `{frontend_url}/toolkit?inat=connected`

---

### `GET /api/inat/status`

Check if the current user is connected to iNaturalist.

**Authentication:** Required (JWT Bearer token)

**Response (200):**

```json
{
  "data": {
    "connected": true,
    "inat_username": "wildlife_user",
    "inat_user_id": 12345,
    "inat_icon_url": "https://static.inaturalist.org/..."
  },
  "meta": { "request_id": "..." }
}
```

---

### `POST /api/inat/disconnect`

Revoke stored iNaturalist tokens and disconnect the user.

**Authentication:** Required (JWT Bearer token)

**Response (200):**

```json
{
  "data": { "disconnected": true },
  "meta": { "request_id": "..." }
}
```

---

### `POST /api/inat/observations`

Create an iNaturalist observation using the user's stored OAuth token.

**Authentication:** Required (JWT Bearer token)

**Request Body:**

```json
{
  "species_guess": "Kiwi",
  "latitude": -36.848,
  "longitude": 174.763,
  "observed_on": "2026-04-12",
  "description": "Captured by Wildlife Watcher camera trap",
  "geoprivacy": "obscured"
}
```

---

### `GET /api/inat/observations/{observation_id}/status`

Poll identification status for a specific iNaturalist observation. Public endpoint — no auth required.

---

### `POST /api/inat/observations/poll`

Batch poll identification status for up to 200 observation IDs.

**Request Body:**

```json
{
  "observation_ids": [123, 456, 789]
}
```

---

## Image Clustering

Near-duplicate detection for camera trap images using perceptual hashing (dHash) with BK-tree indexing.

### `POST /api/clustering/analyze`

Cluster uploaded images by visual similarity and select representative images.

**Authentication:** None

**Content-Type:** `multipart/form-data`

**Parameters:**

| Field | Type | Description |
|-------|------|-------------|
| `files` | binary[] | One or more image files (JPEG, PNG, WebP) |
| `max_hamming` | int | Similarity threshold (0–20). Lower = stricter. Default 10. |

**Response (200):**

```json
{
  "data": {
    "total_images": 100,
    "total_clusters": 25,
    "total_representatives": 25,
    "clusters": [
      {
        "cluster_id": 0,
        "size": 8,
        "representative": "IMG_0042.jpg",
        "members": [
          {
            "filename": "IMG_0042.jpg",
            "sharpness": 1523.4,
            "width": 1920,
            "height": 1080,
            "is_representative": true
          }
        ]
      }
    ]
  },
  "meta": { "request_id": "..." }
}
```

**Limits:** Maximum 1000 images per request.

---

### `POST /api/clustering/analyze/csv`

Same clustering logic, but returns results as a downloadable CSV file.

**Content-Type:** `multipart/form-data`

**Parameters:** Same as `/api/clustering/analyze`

**Response:** `text/csv` file with columns: `filename`, `cluster_id`, `cluster_size`, `is_representative`, `sharpness`, `width`, `height`

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
