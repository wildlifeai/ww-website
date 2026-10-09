# 00 — Getting Started

Set up the Wildlife Watcher web app locally and verify both services work. This is the
deeper companion to the root [`readme.md`](../../readme.md).

## Architecture in one minute

```
Browser (React + Vite, :5173) ──REST + Supabase JWT──▶ FastAPI (:8000)
                              └──Supabase Auth / data──▶ Supabase (PostgreSQL + RLS, Auth, Storage)
FastAPI ──▶ Azure Blob (temp image buffer) ──▶ async job ──▶ Google Drive (permanent archive)
```

- The **frontend** reads/writes most data **directly from Supabase** (scoped by RLS) and calls the
  **backend** only for work the browser can't do: EXIF parsing, Drive uploads, model conversion,
  the AI pipeline, LoRaWAN webhooks, and a few privileged RPCs.
- The **backend** is layered **router → domain → service** (see [02-CODEBASE-GUIDE](./02-CODEBASE-GUIDE.md)).

## Prerequisites

- Node.js 22 (LTS) or higher
- Python 3.11+
- A Supabase project with the Wildlife Watcher schema (owned by `ww-backend`)
- _(optional)_ Docker + Docker Compose

## 1. Clone and configure environment

Both services share **one `.env` at the repo root** — `frontend/vite.config.ts` loads it from `../`,
and the backend reads `../.env` before `backend/.env`.

```bash
git clone https://github.com/wildlifeai/ww-website.git
cd ww-website
cp .env.example .env          # blank template — or, Wildlife.ai team:
bash scripts/fetch-env.sh     # pull the shared dev .env from Azure Key Vault
```

Team members get vault access from a maintainer — see the readme's
[Secrets & Access](../../readme.md#secrets--access) section for the vault name and grant command.

### Environment variables

Defined and validated in [`backend/app/config.py`](../../backend/app/config.py) — that file is the
**complete, authoritative list**; the tables below are only what you need to run locally. For the
per-subsystem cloud set (storage, AI pipeline, demo, integrations) see the
[full-pipeline config checklist](../resources/deployment-guide.md#full-pipeline-config-checklist-per-subsystem).
Missing **required** values make the backend refuse to start.

**Required**

| Variable | Description |
|----------|-------------|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_ANON_KEY` | Anonymous/public key (used by the browser) |
| `SUPABASE_SERVICE_ROLE_KEY` | Service-role key — bypasses RLS, **keep secret** |

**Common optional**

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_API_BASE_URL` | `http://localhost:8000` | Backend URL the frontend calls |
| `ALLOWED_ORIGINS` | `…,http://localhost:5173` | CORS origins (comma-separated) |
| `RATE_LIMIT_PER_MINUTE` | `60` | Per-IP API rate limit |
| `GOOGLE_DRIVE_ENABLED` | `false` | Async upload of analysed images to Drive |
| `AZURE_STORAGE_CONNECTION_STRING` | _(empty)_ | Required when Drive upload is enabled |
| `SENTRY_DSN` / `LOG_LEVEL` | _/ `info`_ | Error tracking / logging level |

Vite maps root vars into the frontend (`SUPABASE_URL` → `VITE_SUPABASE_URL`, etc.) — you do **not**
need a `frontend/.env`. See [03-DATA-AND-SYNC](./03-DATA-AND-SYNC.md) for the full integration list.

## 2. Backend (FastAPI)

```bash
cd backend
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # optional: tests + linting
uvicorn app.main:app --reload --port 8000
```

## 3. Frontend (React/Vite)

```bash
cd frontend
npm install
npm run dev
```

## 4. Verification checklist

| Check | How | Expected |
|-------|-----|----------|
| Backend up | `curl http://localhost:8000/health` | `{"status":"ok"}` |
| Swagger | open `http://localhost:8000/docs` | interactive API docs |
| Frontend up | open `http://localhost:5173` | landing page loads |
| Auth | click **Login** | Supabase Auth UI appears |
| Signed-in nav | log in | top tabs: **Toolkit · Review · Insights** (the Review tab is `/annotations` in the code) (plus **Realtime** when a deployment is active) |
| Data reads | open **Insights** | your projects / deployments |
| Upload path | log in as a [seed user](../resources/testing-with-seed-users.md) and drag a folder from [`test-fixtures/camera-trap/sdcard/dev-sdcard/MEDIA/`](../../test-fixtures/camera-trap/README.md) into **Upload Data** | pipeline reaches **✅ Done**; the images appear in **Annotations** |

If observation edits fail with `permission denied for table observations`, the database is missing
write GRANTs for the `authenticated` role — that's a `ww-backend` migration, see
[03-DATA-AND-SYNC](./03-DATA-AND-SYNC.md).

## Next steps

- [01-TECHNOLOGY-STACK](./01-TECHNOLOGY-STACK.md) — what's installed and why
- [02-CODEBASE-GUIDE](./02-CODEBASE-GUIDE.md) — how the code is organised
