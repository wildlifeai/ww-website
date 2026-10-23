# Demo account

Wildlife Watcher ships a shared, **read-only demo account** behind the "🔍 Try the
demo" button on the marketing/login page. Anyone can explore real sample camera-trap
data without signing up — and they can't change anything.

- **Account:** `demo@wildlife.ai` (a real Supabase user, flagged `app_metadata.is_demo = true`)
- **Access level:** `project_viewer` on a single curated demo project — browse everything, write nothing
- **Sign-in:** server-side via `POST /api/auth/demo-session` (keeps the password out of the bundle, rate-limited per IP)

---

## Read-only is enforced at three layers

The demo can never mutate data, even though it's a normal signed-in session:

| Layer | Mechanism | Where |
|-------|-----------|-------|
| **Database** | The `project_viewer` role grants `SELECT` on project data; **no write policy accepts it** | `ww-backend` RLS (migration `add_project_viewer_readonly_role`) |
| **API** | The write check refuses a `project_viewer`, so the demo, with **403** on every route that changes project data with the service role ([Access](api-reference.md#access)); `require_not_demo` also returns **403** on the mutating endpoints (CamtrapDP import, pipeline run, effort, annotations, iNat, …) | `backend/app/authz.py`, `backend/app/dependencies.py` |
| **UI** | `DemoGuard` disables write controls with a tooltip, guards write routes, and shows a friendly toast instead of a raw error | `frontend/src/components/common/DemoGuard.tsx` |

A demo visitor therefore sees disabled buttons and "read-only demo" notices — never a
`new row violates row-level security policy` or a 403 in the console.

---

## How to access / test

### In the browser
Click **"🔍 Try the demo"** on the home or login page. The app calls
`/api/auth/demo-session`, stores the returned session, and drops you into the dashboard
with the read-only banner.

### Via the API
The demo session is a normal Supabase JWT — use it as a `Bearer` token against any
**read** endpoint.

```bash
# 1. Mint a demo session (no auth required)
curl -sX POST https://<backend>/api/auth/demo-session | jq .
# → { "data": { "access_token": "<jwt>", "refresh_token": "..." } }

TOKEN=$(curl -sX POST https://<backend>/api/auth/demo-session | jq -r .data.access_token)

# 2. Use it for reads (works)
curl -s https://<backend>/api/brain/clusters/<deployment_id> \
  -H "Authorization: Bearer $TOKEN" | jq .

# 3. Writes are blocked (403)
curl -sX POST https://<backend>/api/pipeline/run \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}'
# → 403  "This action is disabled in the demo…"
```

Backend hosts:
- **Dev:** `https://ww-backend-dev.<...>.azurecontainerapps.io`
- **Staging/prod:** `https://ww-backend.<...>.azurecontainerapps.io`

If the endpoint returns `{"error":{"code":"DEMO_DISABLED"}}`, the server has no
`DEMO_EMAIL` / `DEMO_PASSWORD` configured (see below).

---

## Sample data

The demo project's images are the real camera-trap fixtures in
[`test-fixtures/camera-trap`](../../test-fixtures/camera-trap). They're uploaded to
Supabase Storage and attached as `media` rows on a deployment in the demo project by
[`scripts/seed_demo_media.py`](../../scripts/seed_demo_media.py) — idempotent, so it only
adds what's missing.

---

## How seeding works, per environment

The demo has three pieces: the **auth user**, its **viewer role + project**, and the
**media (images)**. How they're created differs because **dev is rebuilt from scratch on
every deploy** while **staging/prod is not**.

### Dev — automatic

| Piece | How |
|-------|-----|
| Demo **user** + **viewer role** + **project** | Seeded in `ww-backend/supabase/seeds/dev/data.sql`. Dev deploys do a destructive `supabase db reset` + re-seed, so these are recreated every deploy. |
| Demo **media** | The [`update-demo.yml`](../../.github/workflows/update-demo.yml) workflow runs `seed_demo_media.py` on any push to `dev` that touches `test-fixtures/**`, and on demand (the workflow targets dev or staging by branch/input — see below). |

> ⚠️ Because the dev DB reset wipes `media` rows, re-run `update-demo.yml` (manually, or it
> re-fires on the next fixtures change) after a `ww-backend` dev deploy. To make this
> bulletproof, have the dev deploy trigger `update-demo.yml` via `repository_dispatch`.

Requires `DEMO_EMAIL`, `DEMO_PASSWORD`, and `SUPABASE_SERVICE_ROLE_KEY` in the GitHub
**`dev`** environment. `DEMO_PASSWORD` must equal the dev seed's `SEED_USER_PASSWORD`.

### Staging / production — manual, one-time

Staging deploys are **schema-only** (`supabase db push`, no reset, no seed), so the demo
is **not** auto-created there — but because nothing is wiped, you only set it up **once**
and it persists. Steps (against the staging/prod Supabase + the `ww-backend` container app):

1. **Seed the user, viewer role and project** — run the full seeder once:
   ```bash
   SUPABASE_URL=<prod-url> SUPABASE_SERVICE_ROLE_KEY=<prod-key> \
   DEMO_EMAIL=demo@wildlife.ai DEMO_PASSWORD=<strong-password> \
   python scripts/seed_demo.py
   ```
   (or `--ensure-user-only` to just (re)create the login, then grant a viewer role on an
   existing project yourself.)
2. **Seed the images** — handled by [`update-demo.yml`](../../.github/workflows/update-demo.yml):
   it runs `seed_demo_media.py` against **staging** on any push to **`main`** that touches
   `test-fixtures/**`, and on demand (`workflow_dispatch` → environment `staging`). Set the
   `staging` GitHub environment's `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` secrets and a
   `DEMO_PROJECT_ID` **var** (your staging demo project). Since staging isn't reset, the
   images persist — the workflow just keeps them in sync as the fixtures change. (You can
   also run the script directly once if you prefer.)
3. **Configure the backend** — set `DEMO_EMAIL` + `DEMO_PASSWORD` on the `ww-backend`
   container app (env vars) so `/api/auth/demo-session` can sign in.
4. **CORS** is already handled: the production origin `wildlifewatcher.ai` is allowed by
   default and any `*.ww-website.pages.dev` by regex (`backend/app/main.py`).

---

## Configuration & disabling

| Setting | Where | Purpose |
|---------|-------|---------|
| `DEMO_EMAIL`, `DEMO_PASSWORD` | `ww-backend` container app env (per environment) | Credentials `/api/auth/demo-session` signs in as. **Unset either → the endpoint self-disables** (`DEMO_DISABLED`) and the button hides the error. |
| `SUPABASE_SERVICE_ROLE_KEY` | GitHub `dev`/`staging` env (for seeders only) | Lets the seed scripts create the user, role, and media. |
| `app_metadata.is_demo` | on the demo Supabase user | Drives the frontend banner and the backend `require_not_demo` / `get_verified_user` checks. |

To turn the demo off entirely, unset `DEMO_EMAIL`/`DEMO_PASSWORD` on the backend — the
button surfaces a quiet "not configured" state and no demo session can be minted.

---

## Related

- [`scripts/seed_demo.py`](../../scripts/seed_demo.py) — user + viewer role + demo project
- [`scripts/seed_demo_media.py`](../../scripts/seed_demo_media.py) — sample images
- [Testing with Seed Users](./testing-with-seed-users.md) — the dev seed personas
- [Deployment Guide](./deployment-guide.md) — environments, secrets, CORS
