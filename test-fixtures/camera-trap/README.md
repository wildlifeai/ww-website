# Camera-trap test fixtures — developer guide

A fixed, reproducible set of real camera-trap images plus the **deployment / project /
device** rows they belong to, so that on **every fresh dev DB build** a developer can
drag the images into the website and exercise the full real-world path:

> end a deployment in the field (mobile app) → drop the SD-card images into the website →
> the backend matches each image to its deployment from EXIF → AI + annotation pipeline runs.

No images are stored in the database. We only seed the **deployment info**; the media rows
are created when a developer uploads the images — exactly what a real user does.

---

## What's in this folder

```
test-fixtures/camera-trap/
├── README.md                     ← this guide
├── exports/<deployment>.json     ← pasted SQL export(s) used to regenerate fixtures (input)
├── sdcard/<label>/IMAGES.000/    ← ready-to-upload camera images (pre-bound via EXIF)
├── source/<label>/               ← (optional) raw frames that NEED EXIF stamping
├── deployments.json              ← (optional) targets for the manual prepare.py path
└── prepare.py                    ← (optional) stamps source/ → MEDIA/ for the manual path
```

The **generator** lives in `ww-website/scripts/build_seed_fixtures.py`. It writes the seed
SQL into the sibling backend repo:

```
ww-backend/supabase/seeds/dev/fixtures.generated.sql   ← generated, idempotent, committed
```

---

## How it's wired into the dev DB build

`fixtures.generated.sql` is applied **after** the base seed (so the seed org + users exist),
in both environments — no manual step:

| Where | File | What happens |
|-------|------|--------------|
| **Cloud** | `ww-backend/.github/workflows/deploy_cloud_projects.yml` | The "Deploy Schema & Seed (Dev)" step cats `seed.sql` + `dev/data.sql` + **`dev/fixtures.generated.sql`** + `seed_taxa.sql` into one migration and pushes it. Running this workflow (dev) rebuilds the dev DB from scratch **with the template deployments**. |
| **Local** | `ww-backend/scripts/seed-local.sh` | Applies `fixtures.generated.sql` right after `data.sql`. |

The generated SQL recreates the **real** project/device/deployment with their production
UUIDs, but **re-homed to the seed General org (`b0000000-…-000001`) and owned by the seed
admin `a0000000-…-000001` (tui@ww.org)** so the seed test users can see them. It is fully
idempotent (`ON CONFLICT` / `IF NOT EXISTS`).

---

## How a developer uses the template

1. Get a fresh dev DB:
   - **Cloud:** run `deploy_cloud_projects.yml` (target `dev`), **or**
   - **Local:** `supabase db reset && bash scripts/seed-local.sh` (in `ww-backend`).
2. Open the website and **log in as `tui@ww.org`** (she owns the template project).
3. Drag a `sdcard/<label>/IMAGES.000/` folder into the website upload.
4. The backend reads each image's EXIF, matches the deployment, creates the media rows on
   the seeded deployment, and the **AI/annotation pipeline** runs (SpeciesNet → crop →
   BioCLIP, plus DINOv3 embeddings). Iterate on features against the same data every time.

> The pipeline needs the uploaded images to be hosted (the upload buffers them to
> Azure/Drive), which `resolve_media` then fetches for the models. That happens
> automatically as part of the website upload — nothing to configure.

---

## How binding works (EXIF)

`ww-website/backend/app/domain/exif.py → match_deployment` resolves an image to a deployment by:

1. **Exact deployment UUID** from a custom EXIF tag, checked in order
   **`Deployment_ID` (0xF200)** → `UserComment` → `Custom_Data`. ✅ primary, verified.
2. **GPS proximity** (~50 m) to the deployment coordinates. ⚠️ fallback only — see Notes.

**Real Wildlife.ai cameras (WW500) already embed the deployment UUID in `Deployment_ID`.**
That's why the images in `sdcard/` need **no stamping** — they self-bind on upload. The
`source/` + `prepare.py` path below is only for frames that *don't* already carry the UUID.

---

## Adding or updating template deployments (the future workflow)

You don't edit the SQL by hand — you regenerate it. Two input modes:

### A. Offline — paste a SQL export (no prod/staging access needed)

Best when you can run SQL in the cloud console but the DB isn't reachable from your machine.

1. **Get the SD-card images** for the deployment(s) (e.g. copy `MEDIA/<DEPID>/` off the camera
   card or a colleague's export) onto your machine.
2. **Export the deployment info.** In the staging/prod Supabase SQL editor, run (swap the UUID):
   ```sql
   SELECT json_build_object(
     'deployment', (SELECT row_to_json(d) FROM deployments d WHERE d.id = '<DEPLOYMENT_UUID>'),
     'project',    (SELECT json_build_object('id', p.id, 'name', p.name, 'organisation_id', p.organisation_id)
                    FROM projects p JOIN deployments d ON d.project_id = p.id WHERE d.id = '<DEPLOYMENT_UUID>'),
     'device',     (SELECT json_build_object('id', dev.id, 'name', dev.name)
                    FROM devices dev JOIN deployments d ON d.device_id = dev.id WHERE d.id = '<DEPLOYMENT_UUID>')
   ) AS export;
   ```
   Save the result to `exports/<DEPLOYMENT_UUID>.json`. (For multiple deployments, save a JSON
   **array** of these objects.)
3. **Generate:**
   ```bash
   python scripts/build_seed_fixtures.py \
     --from-json test-fixtures/camera-trap/exports/<DEPLOYMENT_UUID>.json \
     --media-dir /path/to/MEDIA/<DEPID>
   ```
   This copies the images into `sdcard/<label>/` and (re)writes `fixtures.generated.sql`.
4. **Commit** `ww-backend/supabase/seeds/dev/fixtures.generated.sql`, the new `sdcard/<label>/`,
   and the `exports/*.json`. Re-run `deploy_cloud_projects.yml` (dev) — the new deployment is now
   part of every dev DB build.

> ⚠️ `fixtures.generated.sql` is regenerated wholesale from **all** records you pass in one run.
> To keep multiple templates, pass them all (a JSON array, or multiple `--from-json`/live runs
> aren't merged) — or hand-merge. The simplest path is to keep one `exports/*.json` array with
> every template deployment and regenerate from it.

### B. Live — pull straight from a Supabase project

When prod/staging **is** reachable from your machine:

```bash
export SUPABASE_URL=https://<env>.supabase.co
export SUPABASE_SERVICE_ROLE_KEY=<service-role-key>          # read-only use
python scripts/build_seed_fixtures.py --project <project-uuid>      # auto-picks deployments
#   or: -d <dep-uuid> -d <dep-uuid> ...    (--limit-deployments N)
```

Live mode pulls the deployment + its project + device. (Downloading the matching photos for
gated/cloud-stored media is handled by the script via `resolve_media`; for camera-native
`sdcard` images you usually already have the files and should use mode A.)

### C. Manual stamped images (frames without an embedded UUID)

For curated frames that **don't** carry `Deployment_ID` (e.g. stock photos), use the legacy path:

```bash
pip install pillow piexif
# 1. Drop JPEGs into source/<label>/ (one folder per entry in deployments.json)
# 2. python prepare.py        → writes MEDIA/<label>/IMAGES.000/*.JPG with EXIF stamped
# 3. Upload MEDIA/<label>/ in the website
```
`prepare.py` writes `UserComment = WW-DEPLOYMENT:<uuid>` (exact match) + GPS + timestamps.
Note: `prepare.py` **wipes and rebuilds `MEDIA/`** from `source/`, so keep pre-bound camera
images in `sdcard/` (which `prepare.py` never touches), not in `MEDIA/`.

---

## Current template inventory

| Label | Project | Deployment | UUID | Images | Notes |
|-------|---------|-----------|------|:------:|-------|
| `tests-in-new-plymouth` | Tests in New Plymouth | User Location | `32f55229-25b3-4887-a253-b2aae9edec05` | 18 | Wildlife.ai WW500, night session (2026-06-08 22:08–23:05), self-bind via `Deployment_ID` |

Owner for all template projects: **tui@ww.org** (`a0000000-…-000001`), General org.

---

## Dev SD-card fixtures (`sdcard/dev-sdcard/`)

The single merged dev SD-card (formerly `tests-in-new-plymouth/` + `access-scenarios/`). Real WW500
images for exercising the full upload → bind → pipeline path **and** the access-control states. Drag
`dev-sdcard/MEDIA/` into the website upload. Each camera folder carries a real `Deployment_ID` UUID in
its EXIF (`0xF200`) — that UUID, not the folder name, is what the backend binds on. **Most folders are
seeded so they bind; only the *not-found* (08702E50) and *no-id* (00000000 / IMAGES.000) cases
deliberately don't.**

> ⚠️ **Log in as a _non-admin_ seed user to test `no_access`** (e.g. Tama Jones, `a0…002`).
> tui (`a0…001`) is a global **`ww_admin`** (`system` scope) and therefore sees *every*
> deployment in *every* org — so the `no_access` case never triggers for her. Use tui only for
> the **valid** / **not-found** / **no-id** cases; switch to a non-admin (General-only member) for
> **no-access**.

| Folder | EXIF `Deployment_ID` | Scenario | Seed? |
|--------|----------------------|----------|-------|
| `MEDIA/7785FABB/` (8 JPG + 8 BMP) | `7785fabb-e00e-4da2-aed6-a0fb906e6d79` | **Valid** — matches a deployment the logged-in user can access → media bind, pipeline runs (night skinks / blanks) | Seed in **General** org; grant the test user a project role |
| `MEDIA/E005A4AB/` (11 JPG + 11 BMP) | `e005a4ab-a287-4efe-9878-56568f5c30bb` | **Valid — diverse content (cats & birds)** — recognisable animals; the best folder for exercising SpeciesNet classification | Seed in **General** org, owner `a0…001` (same as the valid row) |
| `MEDIA/472E4095/` | `472e4095-1dd9-470d-9fae-a244ae3ec681` | **Valid — New Plymouth** | Seeded in **General** org, owner `a0…001` (`access_test_deployments.sql`) |
| `MEDIA/32f55229/` | `32f55229-25b3-4887-a253-b2aae9edec05` | **Valid — New Plymouth night session** | Seeded by `fixtures.generated.sql` |
| `MEDIA/242025DF/` (3 JPG + 3 BMP) | `242025df-5d55-47d6-b245-e1690ef44126` | **No access** — deployment exists but in an org the **non-admin** test user is not in → `no_access` warning | Seeded in org **`b0…003`** (owner `a0…004`); Tama `a0…002` is not a member → no access |
| `MEDIA/08702E50/` (1) | `08702e50-f7c6-499f-8b9d-3ecf9cfe050a` | **Not found** — UUID not in the DB → `not_found` warning | **Do not seed** |
| `MEDIA/EAB2AA23/` (2 JPG) | `eab2aa23-ff7e-4f85-ad5b-00fb43603f1c` | **Not found** — second unseeded-UUID sample → `not_found` warning | Currently unseeded |
| `MEDIA/00000000/` (4) | _(none — tag absent)_ | **No deployment ID** — camera captured while unconfigured → no binding | n/a |
| `MEDIA/IMAGES.000/` (4) | _(none — 2024 epoch date)_ | **Unconfigured camera** — no `Deployment_ID`, no RTC set | n/a |

> **Two matching paths are exercised:** (1) the frontend pre-upload check
> (`AnalyseImages.tsx` → `/api/deployments/validate`) reads the `MEDIA/<8-hex>/` **folder prefix**
> and surfaces the `not_found` / `no_access` banners — which is why the `MEDIA/<HEX>/` structure is
> preserved here; (2) the backend `match_deployment` (`domain/exif.py`) binds each image by the full
> **EXIF UUID**. Both should agree.

> **BMP + JPEG both included.** In the device's diagnostic "Save BMP" mode the camera **alternates**
> one format per frame (even→BMP grayscale-raw, odd→JPG), so a BMP and a JPG are *different* captures,
> not duplicates. The `7785FABB/` and `242025DF/` folders carry both. With `FF_BMP_INGEST_ENABLED` on
> (default in `docker-compose.yml`), the upload route re-compresses each BMP → JPEG at
> `BMP_JPEG_QUALITY` and ingests it as its own frame. **BMP frames have no EXIF**, so they bind via the
> `MEDIA/<8-hex>/` folder prefix + hex-filename timestamp — exercising the folder-bound path alongside
> the EXIF-bound JPGs. See [bmp-ingestion-analysis.md](../../documentation/development%20reports/bmp-ingestion-analysis.md).

### Seed rows to add (the "valid" + "no-access" deployments)

These are *test-scenario* rows (not a real export), and the "no-access" one must live in a
different org — which the generator can't express (it re-homes everything to General). So add them
as a small **hand-written** seed in `ww-backend`, e.g. `supabase/seeds/dev/access_test_deployments.sql`,
cat'd into the dev seed after `data.sql` (same as `fixtures.generated.sql`). Sketch:

```sql
-- VALID: alice (a0…001) can see this — General org, she's a member.
-- A camera belongs to no organisation (ww-backend#346): the project decides who sees the deployment.
INSERT INTO devices (id, name, modified_by, bluetooth_id, device_eui)
VALUES ('d7785fab-0000-4000-8000-000000000001', 'WW500 (valid test)',
        'a0000000-0000-0000-0000-000000000001',
        'BT_TEST_7785', 'EUI_TEST_7785') ON CONFLICT (id) DO NOTHING;
INSERT INTO projects (id, name, description, organisation_id, created_by, modified_by, is_active)
VALUES ('p7785fab-0000-4000-8000-000000000001', 'Access Test — Valid',
        'Upload self-binds; alice has access', 'b0000000-0000-0000-0000-000000000001',
        'a0000000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000001', true)
ON CONFLICT (id) DO NOTHING;
INSERT INTO deployments (id, project_id, device_id, name, location_name, deployment_status_id,
        deployment_start, setup_by, modified_by)
VALUES ('7785fabb-e00e-4da2-aed6-a0fb906e6d79', 'p7785fab-0000-4000-8000-000000000001',
        'd7785fab-0000-4000-8000-000000000001', 'Valid Access Test', 'Test Location', 2,
        '2026-06-12T00:00:00Z', 'a0000000-0000-0000-0000-000000000001',
        'a0000000-0000-0000-0000-000000000001') ON CONFLICT (id) DO NOTHING;

-- NO ACCESS: same shape but org b0…002, owner a0…00b, and alice gets NO role here.
-- (Confirm a0…001 is not a global/ww_admin, or she'd see it anyway.)
INSERT INTO projects (id, name, description, organisation_id, created_by, modified_by, is_active)
VALUES ('p242025d-0000-4000-8000-000000000002', 'Access Test — No Access',
        'Different org; alice is not a member', 'b0000000-0000-0000-0000-000000000002',
        'a0000000-0000-0000-0000-00000000000b', 'a0000000-0000-0000-0000-00000000000b', true)
ON CONFLICT (id) DO NOTHING;
-- + matching device + deployment id 242025df-5d55-47d6-b245-e1690ef44126 in that project.
-- Do NOT grant alice (a0…001) any role on org b0…002 / this project.
```

---

## Notes / known issues

- **GPS fallback is unreliable.** `prepare.py` writes GPS as big-endian (`MM`) via `piexif`,
  but the backend parser (`exif.py _format_value`, the `"<II"` unpack) assumes little-endian, so
  GPS reads back as `None`. The **UUID path is what matters** and works; fix the parser to honour
  the detected endianness if you need the proximity fallback.
- **Curate a mix** when adding templates: animal frames (SpeciesNet detections), blank frames
  (blank handling), and night/IR frames.
- `exports/*.json` holds deployment **metadata only** (no secrets) — safe to commit as the
  regeneration input.
