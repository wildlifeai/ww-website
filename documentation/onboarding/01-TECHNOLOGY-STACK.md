# 01 — Technology Stack

The exact dependencies the web app runs on. Versions are the source of truth in
[`frontend/package.json`](../../frontend/package.json) and
[`backend/requirements.txt`](../../backend/requirements.txt); this guide explains the *why*.

## Frontend

| Area | Package | Version | Notes |
|------|---------|---------|-------|
| Framework | `react` / `react-dom` | 19.2 | Function components + hooks only |
| Build | `vite` | 8 | Dev server + `tsc -b && vite build` |
| Language | `typescript` | ~6.0 | `strict` mode; `tsc -b --noEmit` gate (plain `tsc --noEmit` checks nothing here) |
| Routing | `react-router-dom` | 7.14 | `useSearchParams`, `NavLink`, nested routes |
| Server state | `@tanstack/react-query` | 5 | All backend/API reads (`useQuery`/`useMutation`) |
| Auth/data | `@supabase/supabase-js` | 2 | Direct DB reads/writes (RLS-scoped) + Auth |
| Auth UI | `@supabase/auth-ui-react` | 0.4 | Login screen |
| Charts | `vega-embed` (Vega-Lite) | 7 | All charts — Recharts fully removed |
| Maps | `leaflet` + `react-leaflet` | 1.9 / 5 | Deployment maps |
| Icons / QR | `lucide-react`, `qrcode.react` | — | UI icons, app-store QR |
| Tooling | `eslint` 9, `typescript-eslint` 8, `husky` 9, `lint-staged` 16 | — | Lint + pre-commit |

**Conventions**: no Tailwind — components use inline `style={{}}` objects with CSS variables
(`--primary`, `--surface`, `--border`, `--radius`). Shared primitives live in
`src/components/ui/` (`Modal`, `Ribbon`, `DataTable`, `ControlBar`/`FilterSelect`, `StatusBadge`,
`VegaChart`).

## Backend

| Area | Package | Notes |
|------|---------|-------|
| Framework | `fastapi` + `uvicorn` | ASGI, async throughout |
| Runtime | Python 3.11+ | |
| Validation | `pydantic` (+ `pydantic-settings`) | request/response models + env validation |
| DB client | `supabase` (supabase-py) | service-role + per-request user clients |
| Jobs | `arq` worker + in-process `asyncio` fallback | `REDIS_URL` set → jobs offload to the ARQ worker; unset → in-process. Cloud dev runs the worker; local dev runs in-process |
| Storage | `azure-storage-blob`, Google Drive API | temp buffer + permanent archive |
| HTTP | `httpx` + `tenacity` | retrying external calls |
| AI | SpeciesNet, DINOv3 (Wildlife Brain), `ethos-u-vela` | inference, embeddings, model conversion |
| Logging | `structlog` | structured JSON logs |
| Errors | `sentry-sdk` | optional, via `SENTRY_DSN` |
| Lint/test | `ruff`, `pytest` | `pyproject.toml` config (line length 100) |

## External services

| Service | Used for | Gate |
|---------|----------|------|
| **Supabase** | PostgreSQL + RLS, Auth, Storage (incl. the public `media-renditions` bucket), RPCs | always |
| **Cloudflare** | Frontend hosting (Pages, per-branch previews), DNS for `wildlifewatcher.ai`, CDN, anonymous web analytics | always (hosting) |
| **Azure** | Backend hosting (Container Apps + ACR); **Blob** = temporary image buffer during upload (deleted after Drive archival); the **ARQ GPU worker** (serverless T4) that runs SpeciesNet / BioCLIP / DINOv3 | hosting; Blob with Drive uploads |
| **Redis** | ARQ job broker between the API and the GPU worker. Runs as an internal **Container App** (`redis:7-alpine`), not Azure Cache for Redis. Unset `REDIS_URL` → jobs run in-process | `REDIS_URL` |
| **Google Drive** | Permanent image archive (`gdrive://` originals) | `GOOGLE_DRIVE_ENABLED` |
| **Vector store** | DINOv3 embeddings for the Wildlife Brain (similarity / clustering). **`pgvector` in the Supabase Postgres** — no new vendor; live since 2026-07-09. Vectors live in `media_embeddings.embedding`; the former Qdrant container has been **removed**. See [Deployment Guide → Vector Store](../resources/deployment-guide.md#vector-store--pgvector-supabase) | `FF_WILDLIFE_BRAIN_ENABLED` |
| **iNaturalist** | Taxa autocomplete + lineage registration, observation publishing + community-ID sync | `FF_INAT_ENABLED` |
| **TTN / Chirpstack** | LoRaWAN uplink webhooks | `FF_LORAWAN_WEBHOOKS_ENABLED` |
| **Sentry** | Error tracking | `SENTRY_DSN` |
| **Edge Impulse** | Trains Species Brains (on-camera classifiers) from an Annotations selection: Studio + ingestion APIs driven by `services/edge_impulse.py`. Without credentials the action packages a dataset ZIP instead | `FF_MODEL_TRAINING_ENABLED` + `EDGE_IMPULSE_API_KEY` / `EDGE_IMPULSE_PROJECT_ID` (set on the **worker** too) |

## Feature flags

Toggle behaviour without code changes (defined in `backend/app/config.py`):

| Flag | Default | Controls |
|------|---------|----------|
| `FF_INAT_ENABLED` | `false` | iNaturalist endpoints |
| `FF_ML_ENABLED` | `false` | ML-assisted classification — **must** be true or `build_pipeline_steps()` returns `[]` |
| `FF_CLUSTERING_ENABLED` | `false` | ⚠️ **Declared but not wired** — nothing reads it; `/api/clustering` (legacy perceptual-hash) is registered unconditionally in `main.py`. Either gate the router or drop the flag |
| `FF_LORAWAN_WEBHOOKS_ENABLED` | `true` | LoRaWAN webhook ingestion |
| `FF_PUBLIC_API_ENABLED` | `false` | Public data API (`/api/v1/*`) |
| `FF_CAMTRAPDP_IMPORT_ENABLED` | `true` | CamtrapDP package import |
| `FF_PIPELINE_ENABLED` | `false` | AI pipeline inference endpoints |
| `FF_SPECIESNET_ENABLED` | `false` | SpeciesNet detector+classifier step |
| `FF_BIOCLIP_ENABLED` | `false` | BioCLIP secondary/zero-shot classifier step |
| `FF_PER_CROP_CLASSIFY_ENABLED` | `false` | One AI observation per detection (not collapsed per image) — requires the GPU worker; see [04-AI-PIPELINE](./04-AI-PIPELINE.md) |
| `FF_EDGE_REFLECT_ENABLED` | `false`¹ | Reflect the camera's on-device (Camera AI) EXIF predictions as `ai_origin='edge'` observations (¹ **on** for dev). See [dual-ai-production-rollout](../resources/dual-ai-production-rollout.md) |
| `FF_MOTION_ROI_FALLBACK_ENABLED` | `false` | Motion-ROI crop fallback when SpeciesNet finds no bbox |
| `FF_WILDLIFE_BRAIN_ENABLED` | `false` | DINOv3 embedding / clustering / similarity |
| `FF_LOCAL_EMBEDDING_ENABLED` | `false` | Accept client-computed (WebGPU) embedding vectors |
| `FF_MEDIA_REGISTRY_ENABLED` | `false` | Thumbnail/crop generation + resolve endpoints |
| `FF_ACTIVE_LEARNING_ENABLED` | `false` | Active-learning review queue + QA report |
| `FF_INTELLIGENCE_ENABLED` | `false` | Conservation-intelligence endpoints (health, alerts, shift) |
| `FF_MODEL_TRAINING_ENABLED` | `false` | Annotations → *Create species ID model…* (Edge Impulse training or dataset export). Set on the ARQ worker as well; see [species-brain-training-spec](../development%20reports/species-brain-training-spec.md) |
| `FF_BMP_INGEST_ENABLED` | `false`¹ | Raw-BMP ingest → JPEG re-compress on upload (¹ compose default: `true`) |

> Always confirm the current set against `config.py` — flags are added as features land.
