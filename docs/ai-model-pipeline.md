# AI Model & Manifest Pipeline

> Architecture guide for the Wildlife Watcher AI model upload, conversion, storage, and manifest generation systems.

---

## Table of Contents

- [Overview](#overview)
- [Storage Architecture](#storage-architecture)
- [Upload Pipeline](#upload-pipeline)
  - [Custom Upload (Edge Impulse)](#custom-upload-edge-impulse)
  - [Pre-trained Model (GitHub Zoo)](#pre-trained-model-github-zoo)
  - [SenseCap Model (SSCMA Zoo)](#sensecap-model-sscma-zoo)
- [Database Schema](#database-schema)
- [Manifest Generation](#manifest-generation)
  - [Project-based Manifest](#project-based-manifest)
  - [Legacy Source Manifest](#legacy-source-manifest)
- [File Naming Convention (8.3 Format)](#file-naming-convention-83-format)
- [Key Domain Functions](#key-domain-functions)
- [Frontend Integration](#frontend-integration)
- [Troubleshooting](#troubleshooting)

---

## Overview

The AI model pipeline handles three operations:

1. **Upload & Convert** — Accept a model from various sources, compile it for the Ethos-U55 NPU via Vela, and store the result as two independent files in Supabase Storage.
2. **Register** — Create or update a record in the `ai_models` table with paths to the stored files.
3. **Manifest Generation** — Assemble a `MANIFEST.zip` containing the model, labels, camera config, and Himax firmware for SD card deployment.

```
┌─────────────────────────────────────────────────────────┐
│                    Model Sources                         │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │ Edge     │  │ GitHub Zoo   │  │ SSCMA (SenseCap)  │  │
│  │ Impulse  │  │ (Pre-trained)│  │ Model Zoo         │  │
│  │ ZIP      │  │              │  │                   │  │
│  └────┬─────┘  └──────┬───────┘  └────────┬──────────┘  │
│       │               │                   │              │
│       ▼               ▼                   ▼              │
│  ┌─────────────────────────────────────────────────────┐ │
│  │         Vela Conversion (if needed)                 │ │
│  │         Extract labels from header / config         │ │
│  └───────────────────┬─────────────────────────────────┘ │
│                      │                                    │
│                      ▼                                    │
│  ┌─────────────────────────────────────────────────────┐ │
│  │         Dual-File Upload to Supabase Storage        │ │
│  │         {fw_id}V{version}.TFL  (model binary)       │ │
│  │         {fw_id}V{version}.TXT  (labels file)        │ │
│  └───────────────────┬─────────────────────────────────┘ │
│                      │                                    │
│                      ▼                                    │
│  ┌─────────────────────────────────────────────────────┐ │
│  │         Database Registration (ai_models table)     │ │
│  │         model_path → TFL storage path               │ │
│  │         labels_path → TXT storage path              │ │
│  └─────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

---

## Storage Architecture

All model files are stored as **independent blobs** in the `ai-models` Supabase Storage bucket. There is no ZIP packaging.

### Storage Path Patterns

| Source | Path Pattern | Example |
|--------|-------------|---------|
| Custom Upload (via `upload_and_register`) | `{org_id}/{name}-custom-{version}/{stem}.TFL` | `b0...01/Rat_Detector-custom-1.0.0/20V1.TFL` |
| Custom Upload (via `convert_model_job`) | `{org_id}/{firmware_id}/{version}/{stem}.TFL` | `b0...01/20/1.0.0-abc123/20V1.TFL` |

Each model has **two files** at the same path prefix:
- `{stem}.TFL` — Compiled TFLite model binary (Vela-optimized for Ethos-U55)
- `{stem}.TXT` — Line-separated classification labels

---

## Upload Pipeline

All uploads are **async jobs**. The API returns a `job_id` immediately, and the client polls `GET /api/jobs/{job_id}` for progress.

### Custom Upload (Edge Impulse)

**Endpoint:** `POST /api/models/convert`

**Flow:**

1. Frontend sends the ZIP file + `model_name` via multipart form.
2. Router validates the file, resolves the user's organisation, creates an `ai_model_families` record (if new), auto-versions the model, and inserts a placeholder `ai_models` row with `status: "uploading"`.
3. The file is stored in Redis blob store and a background job (`convert_model_job`) is enqueued.
4. The worker:
   - Retrieves the blob from Redis.
   - Calls `convert_uploaded_model()` which extracts the ZIP, runs Vela conversion, and returns `(tfl_bytes, txt_bytes, labels)`.
   - Builds 8.3 filenames from the model family's `firmware_model_id` and version.
   - Uploads both files to Supabase Storage.
   - Updates the `ai_models` row to `status: "validated"` with the final storage paths and file hash.
   - Cleans up the Redis blob.

**Accepted file types:** `.zip` (Edge Impulse export), `.tflite` (raw TFLite), `.cc` (C array)

### Pre-trained Model (GitHub Zoo)

**Endpoint:** `POST /api/models/pretrained` with `source_type: "pretrained"`

**Flow:**

1. Frontend selects an architecture and resolution from the catalog (fetched from `GET /api/models/pretrained/catalog`).
2. A background job (`download_github_pretrained_job`) is enqueued.
3. The worker:
   - Calls `convert_github_pretrained_model()` which downloads the model from the GitHub raw URL, handles C-array parsing (for `.cc` files) or Vela conversion (for `.tflite`), and returns `(tfl_bytes, txt_bytes, labels, metadata)`.
   - Calls `upload_and_register()` which resolves the model family, builds 8.3 filenames, uploads to storage, and inserts the `ai_models` row.

**Available models** are defined in `backend/app/registries/model_registry.py`:

| Architecture | Firmware ID | Resolutions | Type |
|---|---|---|---|
| Person Detection | 20 | 96×96 | `cc_array` |
| YOLOv8 Object Detection | 1 | 192×192 | `tflite` |
| YOLOv11 Object Detection | 1 | 192×192, 224×224 | `tflite` |
| YOLOv8 Pose Estimation | 3 | 256×256 | `tflite` |

### SenseCap Model (SSCMA Zoo)

**Endpoint:** `POST /api/models/pretrained` with `source_type: "sscma"`

**Flow:**

1. Frontend selects a model from the SSCMA catalog (fetched from `GET /api/models/sscma/catalog`).
2. A background job (`download_pretrained_job`) is enqueued.
3. The worker:
   - Calls `convert_pretrained_model()` which downloads from the SSCMA zoo, runs Vela if needed, and returns `(tfl_bytes, txt_bytes, labels, metadata)`.
   - Calls `upload_and_register()` to store and register.

> **Note:** SSCMA models do not have a pre-assigned `firmware_model_id`. They receive a fallback ID of `9999` unless a family with a matching name already exists with a firmware ID assigned.

---

## Database Schema

### `ai_model_families`

Groups related model versions together. Each family has a unique `firmware_model_id` used for 8.3 filename generation.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `organisation_id` | UUID | FK → `organisations` |
| `name` | text | Display name (e.g. "Person Detection (96x96)") |
| `firmware_model_id` | integer | Unique numeric ID used in firmware filenames |

### `ai_models`

Individual model versions with storage paths and metadata.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `model_family_id` | UUID | FK → `ai_model_families` |
| `organisation_id` | UUID | FK → `organisations` |
| `name` | text | Display name |
| `version` | text | Semantic version string (e.g. "1.0.0-abc123") |
| `version_number` | integer | Major version integer (used by firmware) |
| `model_path` | text | Supabase Storage path to `.TFL` file |
| `labels_path` | text | Supabase Storage path to `.TXT` file |
| `status` | text | `uploading` → `validating` → `validated` → `deployed` / `failed` |
| `file_hash` | text | SHA-256 hash of the `.TFL` binary |
| `file_size_bytes` | integer | Combined size of TFL + TXT |
| `detection_capabilities` | jsonb | Array of label strings |
| `processing_log` | jsonb | Array of status transition entries |

**Status Lifecycle:**

```
uploading → validating → validated → deployed
                ↓
              failed
```

---

## Manifest Generation

The manifest is a ZIP file deployed to the camera's SD card containing everything the device needs.

### MANIFEST.zip Structure

```
MANIFEST/
├── CONFIG.TXT          # Camera configuration (opcodes)
├── {fw_id}V{ver}.TFL   # AI model binary (8.3 format)
├── {fw_id}V{ver}.TXT   # Model labels (8.3 format)
└── output.img          # Himax coprocessor firmware
```

**Endpoint:** `POST /api/manifest/generate`

### Project-based Manifest

When `model_source: "My Project"` and a `project_id` is provided:

1. Fetches the project's assigned `model_id` from the `projects` table.
2. Looks up the model's `firmware_model_id` and `version_number` via the `ai_model_families` join.
3. Downloads the `.TFL` and `.TXT` files directly from Supabase Storage using the model's `model_path` and `labels_path`.
4. Injects firmware opcodes (OP 14 = firmware_model_id, OP 15 = version) into `CONFIG.TXT`.
5. Downloads `output.img` (Himax firmware) from the `firmware` bucket.
6. Packages everything into `MANIFEST.zip`.

### Legacy Source Manifest

For `model_source` values of `github`, `sscma`, `organisation`, or `default`:

- **`github`**: Downloads and compiles the model on-the-fly from the model registry.
- **`organisation`**: Fetches the model directly from Supabase Storage using `model_path` and `labels_path`.
- **`default`**: Queries the `ai_models` table for the best available model (Person Detection preferred).

---

## File Naming Convention (8.3 Format)

The camera's SD card reader enforces the **8.3 filename format** (max 8-character name + 3-character extension). Model files are named using:

```
{firmware_model_id}V{version_number}.TFL
{firmware_model_id}V{version_number}.TXT
```

**Examples:**

| firmware_model_id | version_number | TFL Filename | TXT Filename |
|---|---|---|---|
| 20 | 1 | `20V1.TFL` | `20V1.TXT` |
| 1 | 3 | `1V3.TFL` | `1V3.TXT` |
| 9999 | 1 | `9999V1.TFL` | `9999V1.TXT` |

If the combined stem exceeds 8 characters, it is truncated (e.g. `12345V99` → `12345V99`; `123456V99` → `123456V9`).

---

## Key Domain Functions

### `backend/app/domain/model.py`

| Function | Description |
|----------|-------------|
| `resolve_or_create_model_family()` | Shared helper to find or create an `ai_model_families` record. Returns `(family_id, firmware_model_id)`. |
| `convert_uploaded_model()` | Extracts an Edge Impulse ZIP, runs Vela, returns `(tfl_bytes, txt_bytes, labels)`. |
| `convert_pretrained_model()` | Downloads an SSCMA model, optionally runs Vela, returns `(tfl_bytes, txt_bytes, labels, metadata)`. |
| `convert_github_pretrained_model()` | Downloads a GitHub zoo model, handles `.cc` arrays and `.tflite`, returns `(tfl_bytes, txt_bytes, labels, metadata)`. |
| `upload_and_register()` | Resolves model family → builds 8.3 filenames → uploads TFL+TXT to storage → inserts/updates `ai_models` row. |

### `backend/app/jobs/definitions.py`

| Function | Description |
|----------|-------------|
| `convert_model_job()` | Worker for custom uploads. Retrieves blob, converts, uploads, updates DB. |
| `download_pretrained_job()` | Worker for SSCMA models. Downloads, converts, registers. |
| `download_github_pretrained_job()` | Worker for GitHub zoo models. Downloads, converts, registers. |
| `generate_manifest_job()` | Worker for manifest assembly. Downloads all components, builds MANIFEST.zip. |

### `backend/app/registries/model_registry.py`

Static registry of pre-trained models with GitHub download URLs, firmware IDs, and classification labels. Exposed to the frontend via `GET /api/models/pretrained/catalog`.

---

## Frontend Integration

### Upload Model Page (`UploadModel.tsx`)

Three model source modes:

1. **Custom Upload** — Drag-and-drop `.zip`/`.tflite`/`.cc` file with a model name.
2. **Pre-trained Model** — Select architecture and resolution from the backend catalog (`GET /api/models/pretrained/catalog`).
3. **SenseCap Models** — Select from the SSCMA zoo (`GET /api/models/sscma/catalog`).

All modes submit an async job and render a `<JobProgress />` component to track it.

### Generate Manifest Page (`GenerateManifest.tsx`)

1. User selects an organisation and project.
2. Frontend resolves the project's assigned model and validates that it has a `firmware_model_id` and `version_number`.
3. Submits `POST /api/manifest/generate` with `model_source: "My Project"`.
4. Polls the job and displays a download link on completion.

---

## Troubleshooting

### "Model X is missing firmware IDs"

The frontend checks that the model's `ai_model_families.firmware_model_id` and `ai_models.version_number` are both set. This error means one or both are `NULL`.

**Fix:** Ensure the model was uploaded through the current pipeline (which populates both fields). For legacy models, manually update:

```sql
-- Set firmware_model_id on the family
UPDATE ai_model_families SET firmware_model_id = 20 WHERE id = '<family-id>';

-- Set version_number on the model
UPDATE ai_models SET version_number = 1 WHERE id = '<model-id>';
```

### "Job failed: Storage upload failed"

The Supabase Storage upload failed. Common causes:
- Storage bucket `ai-models` doesn't exist or has restrictive policies.
- File already exists and `upsert` is not enabled.
- Network timeout to Supabase.

### "No suitable TFLite benchmark found"

When importing an SSCMA model, the model's benchmark list doesn't contain a TFLite or TFLite(vela) entry. This model may not be compatible with the Ethos-U55 NPU.
