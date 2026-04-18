# Wildlife Watcher V2 Backend

Production-grade FastAPI backend powering the Wildlife Watcher platform. Handles firmware manifest generation, AI model conversion, LoRaWAN webhook ingestion, and EXIF-based image analysis — all with local asyncio background tasks and Supabase persistence.

## Table of Contents

- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Configuration](#configuration)
- [Running Locally](#running-locally)
- [Testing](#testing)
- [API Overview](#api-overview)
- [Async Job System](#async-job-system)
- [LoRaWAN Integration](#lorawan-integration)
- [Domain Layer Guide](#domain-layer-guide)
- [Services Guide](#services-guide)
- [Middleware Stack](#middleware-stack)
- [Contributing](#contributing)

---

## Quick Start

### Prerequisites

- **Python 3.11+**
- **Docker** and **Docker Compose** (for full stack)
- A **Supabase** project with the Wildlife Watcher schema

### Option 1: Docker Compose (recommended)

```bash
# From the repository root (ww-website/)
cp .env.example .env
# Edit .env with your Supabase keys

# Start all services
docker compose up -d

# Verify
curl http://localhost:8000/health
# → {"status": "ok"}

# View logs
docker compose logs -f api
```

### Option 2: Local Python

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # or .\venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Copy .env to backend root
cp ../.env.example .env
# Edit with your Supabase keys

# Start the API server
uvicorn app.main:app --reload --port 8000

```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Clients                                  │
│    Mobile App (React Native)  │  Web Frontend  │  LoRaWAN NS    │
└──────────────┬────────────────┴───────┬────────┴────────┬───────┘
               │                        │                  │
               ▼                        ▼                  ▼
┌──────────────────────────────────────────────────────────────────┐
│                      FastAPI (API Server)                        │
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐│
│  │  Routers │→ │  Domain  │  │ Middleware│  │   Dependencies   ││
│  │ (thin)   │  │ (logic)  │  │ (cross-  │  │   (auth, DI)     ││
│  │          │  │          │  │  cutting) │  │                  ││
│  └────┬─────┘  └────┬─────┘  └──────────┘  └──────────────────┘│
│       │              │                                           │
│       ▼              ▼                                           │
│  ┌────────────────────────────────────────────────────────────┐│
│  │                     Services Layer                         ││
│  │  Supabase · HTTP · Storage · Vela · Cache · DB Utils       ││
│  └────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────┘
               │                                    │
               ▼                                    ▼
┌──────────────────────────┐       ┌───────────────────────────────┐
│   Supabase (PostgreSQL)  │       │     ARQ Worker (separate      │
│   + Supabase Storage     │       │     container/process)        │
│   + Supabase Realtime    │       │                               │
└──────────────────────────┘       │  convert_model_job            │
                                   │  generate_manifest_job        │
                                   │  upload_drive_images          │
                                   └───────────────────────────────┘
```

### Design Principles

1. **Domain-Driven Separation** — Business logic lives in `domain/`, HTTP concerns live in `routers/`, infrastructure in `services/`. Each layer is independently testable.

2. **Async-First** — Heavy operations (Vela conversion, manifest assembly, downloads) run in an in-process asyncio background loop to conserve resources. The API returns a `job_id` immediately.

3. **Observability by Default** — Every request gets a UUID (`X-Request-ID`), structured JSON logging, and optional Sentry error tracking.

4. **Fail-Fast Config** — All environment variables are validated at startup via Pydantic. Missing required vars → app refuses to boot.

---

## Project Structure

```
backend/
├── app/
│   ├── __init__.py
│   ├── config.py               # Pydantic BaseSettings — env vars
│   ├── dependencies.py         # FastAPI Depends — auth, Supabase DI
│   ├── main.py                 # App entry — CORS, lifespan, middleware
│   │
│   ├── domain/                 # Business logic (no HTTP, no Streamlit)
│   │   ├── exif.py             # JPEG EXIF parsing + deployment matching
│   │   ├── lorawan.py          # LoRaWAN uplink processing pipeline
│   │   ├── manifest.py         # MANIFEST.zip assembly
│   │   ├── model.py            # Vela conversion + upload/register
│   │   └── photo_preprocessing.py  # GPS→local time, Drive folder/file naming
│   │
│   ├── jobs/                   # Async job system (Local Loop + Supabase)
│   │   ├── definitions.py      # Job functions (run by worker)
│   │   ├── store.py            # Redis-backed job status read/write
│   │   └── worker.py           # ARQ WorkerSettings
│   │
│   ├── middleware/             # Cross-cutting concerns
│   │   ├── logging.py          # Structured JSON request logging
│   │   ├── rate_limit.py       # slowapi rate limiting
│   │   └── request_id.py       # X-Request-ID propagation
│   │
│   ├── registries/             # Static configuration data
│   │   ├── camera_configs.py   # Supported camera hardware
│   │   └── model_registry.py   # Pre-trained model download URLs
│   │
│   ├── routers/                # HTTP endpoints (thin — validate + delegate)
│   │   ├── exif.py             # POST /api/exif/parse
│   │   ├── jobs.py             # GET  /api/jobs/{id}
│   │   ├── lorawan.py          # POST /api/lorawan/webhook/*
│   │   ├── manifest.py         # POST /api/manifest/generate
│   │   └── models.py           # POST /api/models/convert
│   │
│   ├── schemas/                # Pydantic models (request/response)
│   │   ├── common.py           # ApiResponse, ApiError, ApiMeta
│   │   ├── job.py              # JobStatus, JobInfo
│   │   ├── lorawan.py          # TTNUplink, ChirpstackUplink
│   │   ├── manifest.py         # ManifestRequest
│   │   └── model.py            # ModelUpload, ModelConvert
│   │
│   └── services/               # Infrastructure adapters
│       ├── azure_storage.py    # Azure Blob Storage (temp image buffer)
│       ├── blob_store.py       # Local disk temp file storage
│       ├── cache.py            # Simple thread-safe dict cache
│       ├── db_utils.py         # Paginated Supabase queries
│       ├── google_drive.py     # Google Drive upload + dedup
│       ├── http_client.py      # httpx + tenacity retry
│       ├── storage.py          # Supabase Storage download/upload
│       ├── supabase_client.py  # Client factories (anon/service-role)
│       └── vela.py             # Vela CLI subprocess wrapper
│
├── tests/
│   ├── conftest.py             # Fixtures + env stubs
│   ├── test_exif_domain.py     # 14 tests
│   ├── test_lorawan_domain.py  # 16 tests
│   ├── test_manifest_domain.py # 7 tests
│   ├── test_model_domain.py    # 9 tests
│   └── test_routers.py         # 5 tests
│
├── Dockerfile                  # Multi-stage (base/dev/worker)
├── pyproject.toml              # ruff + pytest config
├── requirements.txt            # Production dependencies
└── requirements-dev.txt        # Test/dev dependencies
```

---

## Configuration

All configuration is managed via environment variables, validated at startup by Pydantic.

### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `SUPABASE_URL` | Supabase project URL | `https://xxx.supabase.co/` |
| `SUPABASE_ANON_KEY` | Public/anonymous API key | `eyJ...` |
| `SUPABASE_SERVICE_ROLE_KEY` | Service-role key (bypasses RLS) | `eyJ...` |

### Optional Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ALLOWED_ORIGINS` | `https://wildlifewatcher.ai,http://localhost:5173` | CORS origins (comma-separated) |
| `RATE_LIMIT_PER_MINUTE` | `60` | Per-IP API rate limit |
| `SENTRY_DSN` | _(none)_ | Sentry error tracking DSN |
| `LOG_LEVEL` | `info` | Logging level (`debug`, `info`, `warning`, `error`) |
| `GENERAL_ORG_ID` | `b0000000-0000-0000-0000-000000000001` | Default organisation UUID |

### Google Drive Integration

| Variable | Default | Description |
|----------|---------|-------------|
| `GOOGLE_DRIVE_ENABLED` | `false` | Enable async uploads of analysed images to Google Drive |
| `GOOGLE_DRIVE_FOLDER_ID` | `1jIWV3...` | Root Google Drive folder ID for uploads |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | _(none)_ | Path to service account JSON file, or inline JSON string |
| `GOOGLE_DRIVE_MAX_FILE_SIZE_MB`| `50` | Max file size in MB accepted for Drive upload |

### LoRaWAN Webhook Secrets

| Variable | Default | Description |
|----------|---------|-------------|
| `LORAWAN_WEBHOOK_SECRET` | _(empty)_ | Fallback secret for all webhook types |
| `LORAWAN_TTN_WEBHOOK_SECRET` | _(empty)_ | TTN-specific secret (takes priority over generic) |
| `LORAWAN_CHIRPSTACK_WEBHOOK_SECRET` | _(empty)_ | Chirpstack-specific secret |

> **Note:** When a secret is empty, the webhook endpoint accepts all requests (development mode). Always set secrets in production.

### Feature Flags

| Variable | Default | Description |
|----------|---------|-------------|
| `FF_INAT_ENABLED` | `false` | Enable iNaturalist integration |
| `FF_ML_ENABLED` | `false` | Enable ML-assisted classification |
| `FF_CLUSTERING_ENABLED` | `false` | Enable ML clustering |
| `FF_LORAWAN_WEBHOOKS_ENABLED` | `true` | Enable LoRaWAN webhook endpoints |
| `FF_PUBLIC_API_ENABLED` | `false` | Enable public data API |

---

## Running Locally

### Development with Docker

```bash
# Build and start with hot-reload
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

# This gives you:
# - API at http://localhost:8000 (auto-reloads on code changes)
# - Swagger docs at http://localhost:8000/docs
# - ReDoc at http://localhost:8000/redoc
```

### Development without Docker

```bash
cd backend
uvicorn app.main:app --reload --port 8000

```

### Accessing the API

Once running, visit:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **OpenAPI JSON**: http://localhost:8000/openapi.json
- **Health Check**: http://localhost:8000/health

---

## Testing

### Running Tests

```bash
cd backend

# Run all tests
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_exif_domain.py -v

# Run with coverage
python -m pytest tests/ --cov=app --cov-report=term-missing

```

### Test Organisation

| File | Tests | What's Covered |
|------|-------|---------------|
| `test_exif_domain.py` | 14 | JPEG parsing, deployment ID extraction, GPS matching |
| `test_lorawan_domain.py` | 16 | Payload parsing, schema validation, webhook secrets |
| `test_manifest_domain.py` | 7 | C hex array parsing, directory flattening |
| `test_model_domain.py` | 9 | ZIP name parsing, label extraction, 8.3 filenames |
| `test_routers.py` | 5 | Health check, OpenAPI schema, smoke tests |

### Writing New Tests

- Domain tests go in `tests/test_<domain>_domain.py`
- Router integration tests go in `tests/test_routers.py`
- Use `conftest.py` fixtures for the FastAPI test client
- Mock Supabase calls using `unittest.mock.patch`

---

## API Overview

All endpoints return responses in a standard envelope:

```json
{
  "data": { ... },
  "error": null,
  "meta": {
    "request_id": "a1b2c3d4-e5f6-..."
  }
}
```

See [docs/api-reference.md](../docs/api-reference.md) for the full endpoint reference.

---

## Async Job System

The backend uses an **in-process Asyncio Runner** backed by **Supabase** for persistence. This allows it to run in a single container (e.g. Azure Container Apps) while safely recovering uncompleted jobs on reboot. 

### How It Works

```
Client                    API Server                 Redis                     Worker
  │                          │                         │                         │
  │  POST /api/models/convert│                         │                         │
  │─────────────────────────>│                         │                         │
  │                          │ 1. Validate upload      │                         │
  │                          │ 2. Store blob in local Temp  │                         │
  │                          │────────────────────────>│                         │
  │                          │ 3. Create job record    │                         │
  │                          │────────────────────────>│                         │
  │                          │ 4. Enqueue locally       │                         │
  │                          │────────────────────────>│                         │
  │  { job_id: "abc-123" }   │                         │                         │
  │<─────────────────────────│                         │                         │
  │                          │                         │  5. Worker picks up job │
  │                          │                         │────────────────────────>│
  │                          │                         │                         │ 6. Retrieve blob
  │                          │                         │<────────────────────────│
  │                          │                         │                         │ 7. Convert model
  │                          │                         │                         │ 8. Upload to Supabase
  │                          │                         │          progress: 0.8  │
  │                          │                         │<────────────────────────│
  │                          │                         │                         │ 9. Store signed URL
  │  GET /api/jobs/abc-123   │                         │   status: "completed"   │
  │─────────────────────────>│                         │<────────────────────────│
  │                          │─────────────────────────>                         │
  │  { status: "completed",  │                         │                         │
  │    progress: 1.0,        │                         │                         │
  │    result_url: "..." }   │                         │                         │
  │<─────────────────────────│                         │                         │
```

### Job Lifecycle

| Status | Description |
|--------|-------------|
| `queued` | Job created, waiting for worker |
| `processing` | Worker actively running the job |
| `completed` | Success — `result_url` available |
| `failed` | Error — `error` field has details |

### Polling for Results

```javascript
// Frontend example
const pollJob = async (jobId) => {
  while (true) {
    const response = await fetch(`/api/jobs/${jobId}`);
    const { data } = await response.json();

    if (data.status === 'completed') {
      // Download result from data.result_url
      return data.result_url;
    }

    if (data.status === 'failed') {
      throw new Error(data.error);
    }

    // Show progress
    updateProgressBar(data.progress);

    // Poll every 2 seconds
    await new Promise(r => setTimeout(r, 2000));
  }
};
```

### Job TTL

Job status records auto-expire after **24 hours**. Temporary blobs (uploaded files) expire after **1 hour**. Result files in Supabase Storage are accessible via signed URLs that expire after **15 minutes**.

---

## LoRaWAN Integration

The backend receives real-time device data via LoRaWAN webhook endpoints. Supported network servers:

| Server | Endpoint | Payload Format |
|--------|----------|----------------|
| TTN v3 | `POST /api/lorawan/webhook/ttn` | [TTN Webhook Spec](https://www.thethingsindustries.com/docs/integrations/webhooks/) |
| Chirpstack v4 | `POST /api/lorawan/webhook/chirpstack` | [Chirpstack HTTP Integration](https://www.chirpstack.io/docs/chirpstack/integrations/http.html) |

### Data Flow

```
Camera → LoRa Radio → Gateway → Network Server → Webhook → API
                                                              │
                                                              ▼
                                                    LoRaWAN Domain
                                                    ├── Parse binary payload
                                                    ├── Match device by EUI
                                                    ├── Find active deployment
                                                    ├── Insert into lorawan_messages
                                                    └── Insert into lorawan_parsed_messages
                                                              │
                                                              ▼
                                                    Supabase Realtime
                                                    (auto-broadcasts to mobile app)
```

### Wildlife Watcher Binary Payload

The firmware sends a compact binary frame:

| Byte | Field | Range | Description |
|------|-------|-------|-------------|
| 0 | Battery Level | 0–100 | Battery percentage |
| 1 | SD Card Usage | 0–100 | SD card capacity used % |
| 2+ | Model Output | variable | JSON or raw binary — AI inference results |

See [docs/lorawan-webhook-setup.md](../docs/lorawan-webhook-setup.md) for network server configuration instructions.

---

## Domain Layer Guide

Domain modules contain pure business logic with no HTTP or Streamlit dependencies.

### `domain/exif.py` — EXIF Parsing

Parses JPEG EXIF metadata including custom Wildlife Watcher firmware tags.

```python
from app.domain.exif import parse_exif_from_bytes, match_deployment

# Parse EXIF from JPEG bytes
result = parse_exif_from_bytes(jpeg_bytes)
# → {"DateTime": "2026:04:12 ...", "latitude": -36.8485, "longitude": 174.7633,
#    "deployment_id": "a1b2c3d4-...", "date": "2026:04:12 ..."}

# Cross-reference with deployments
match = match_deployment(result, deployments_list)
# → {"id": "...", "project_id": "...", ...} or None
```

### `domain/manifest.py` — Manifest Generation

Assembles MANIFEST.zip packages for SD card deployment.

```python
from app.domain.manifest import generate_manifest

manifest_bytes = await generate_manifest(
    model_source="github",
    model_type="YOLOv11 Object Detection",
    resolution="192x192",
    camera_type="Raspberry Pi",
)
# → bytes of MANIFEST.zip containing CONFIG.TXT + model files
```

### `domain/model.py` — Model Conversion

Converts Edge Impulse ZIPs through Vela and registers in Supabase.

```python
from app.domain.model import convert_uploaded_model, upload_and_register

# Step 1: Convert
model_bytes, labels = await convert_uploaded_model(zip_content, "mymodel-custom-1.0.zip")
# → (ai_model.zip bytes, ["cat", "dog", "bird"])

# Step 2: Upload and register
record = await upload_and_register(
    model_bytes, "MyModel", "1.0", "Custom model", labels, org_id, user_id
)
# → {"id": "...", "storage_path": "...", ...}
```

### `domain/lorawan.py` — LoRaWAN Processing

Normalises webhook payloads and stores in Supabase.

```python
from app.domain.lorawan import LoRaWANDomain

domain = LoRaWANDomain()
parsed = await domain.process_ttn_uplink(ttn_payload)
# → ParsedMessage(battery_level=75.0, sd_card_used_capacity=42.0, ...)
```

---

## Services Guide

Services are infrastructure adapters — they talk to external systems.

| Service | File | Purpose |
|---------|------|---------|
| `supabase_client` | `supabase_client.py` | Anon/service-role client factories |
| `http_client` | `http_client.py` | Async HTTP with automatic retries (3 attempts, exponential backoff) |
| `storage` | `storage.py` | Supabase Storage upload/download with SDK→public URL fallback |
| `vela` | `vela.py` | Ethos-U Vela CLI subprocess wrapper |
| `cache` | `cache.py` | Redis cache-aside pattern with configurable TTL |
| `blob_store` | `blob_store.py` | Temp file storage in Redis (API → Worker transfer) |
| `db_utils` | `db_utils.py` | Paginated Supabase table queries |

### Cache Pattern

```python
from app.services.cache import cached

# Cached for 1 hour — fetches on first call, returns cached on subsequent
data = await cached("sscma:catalog", ttl=3600, fetch_fn=fetch_from_github)
```

### Storage Pattern

```python
from app.services.storage import download_from_storage, upload_to_storage

# Download (SDK first, public URL fallback)
content = await download_from_storage("firmware", "config/latest.zip")

# Upload
success = await upload_to_storage("ai-models", "org/model/ai_model.zip", bytes_data)
```

---

## Middleware Stack

Middleware executes on every request in order (outermost first):

1. **RequestIDMiddleware** — Generates or propagates `X-Request-ID` header
2. **LoggingMiddleware** — Structured JSON log for every request (method, path, status, duration)
3. **CORSMiddleware** — Cross-origin request handling
4. **Rate Limiting** — Per-IP limits via `slowapi` (configurable via `RATE_LIMIT_PER_MINUTE`)

---

## Contributing

### Code Style

- **Formatter**: [ruff](https://docs.astral.sh/ruff/) (line length 100)
- **Type hints**: Required on all public functions
- **Docstrings**: Google-style on all modules, classes, and public functions

```bash
# Lint
ruff check app/

# Format
ruff format app/

# Type check (optional)
mypy app/
```

### Adding a New Endpoint

1. Create/update the **schema** in `schemas/`
2. Add **domain logic** in `domain/` (no HTTP imports!)
3. Create the **router** in `routers/` (thin — validate then delegate)
4. Register the router in `main.py`
5. Write **tests** in `tests/`

### Adding a New Async Job

1. Define the job function in `jobs/definitions.py`
2. Register it in the `JOBS` list at the bottom of the same file
3. Create the job from the router: `job_id = await create_job()`
4. Enqueue via: `await request.app.state.arq_pool.enqueue_job("job_name", ...)`
5. The worker picks it up automatically — no restart needed (in dev mode with volume mounts)
