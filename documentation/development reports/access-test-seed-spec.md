# Report — Access-Scenario Seed Deployments (ww-backend)

> **Status:** ✅ **Shipped** — seeded by `ww-backend/supabase/seeds/dev/access_test_deployments.sql`.
> Retained as the reasoning behind those rows; the SQL in that repo is authoritative if the two ever
> disagree. Move to `_archive/` at the next docs tidy.

**Audience:** whoever maintains `ww-backend/supabase/seeds`.
**Goal:** seed the deployments that back the `sdcard/dev-sdcard/` fixtures so a fresh dev DB
can reproduce the **valid / no-access / not-found / no-id** upload paths every time.

The fixture images live in
[`ww-website/test-fixtures/camera-trap/sdcard/dev-sdcard/MEDIA/`](../../test-fixtures/camera-trap/README.md#dev-sd-card-fixtures-sdcarddev-sdcard).
Each camera folder carries a real `Deployment_ID` UUID in its EXIF; that UUID is what the backend
binds on. This report specifies the rows to seed for each scenario.

## Why this is hand-written, not generated

`scripts/build_seed_fixtures.py` re-homes every deployment to the **General** org owned by the seed
admin. That's correct for the *valid* case but cannot express the *no-access* case (a deployment in a
**different** org the test user can't see). So these go in a small, idempotent, hand-written file:

```
ww-backend/supabase/seeds/dev/access_test_deployments.sql
```

cat'd into the dev seed **after** `data.sql` (so the orgs/users exist) and after
`fixtures.generated.sql`, in both `seed-local.sh` and `deploy_cloud_projects.yml` — same wiring the
existing fixtures use.

> **✅ Implemented.** This is now seeded by `ww-backend/supabase/seeds/dev/access_test_deployments.sql`,
> applied after `data.sql` + `fixtures.generated.sql` by both `scripts/seed-local.sh` (local) and
> `.github/workflows/deploy_cloud_projects.yml` (cloud). The sketch below documents the reasoning.

## Critical access-model facts (verified in `data.sql`)

- The seed admin **tui `a0000000-…-000001` (`tui@ww.org`) is a global `ww_admin`** (`system` scope) →
  they see **every** deployment in **every** org, so they **cannot** be the subject for *no-access*.
  Use them for valid / not-found / no-id.
- **Tama `a0000000-…-000002`** is the non-admin test user — but he is a member of **both** `b0…001`
  **and** `b0…002`. So the no-access deployment must live in an org Tama is **not** in. Orgs `b0…003`
  and `b0…004` have none of the test users; the seed uses **`b0…003`** (owner `a0…004`, a real member)
  → guaranteed `no_access` for Tama under any RLS interpretation.
- For the *valid* case, grant the test user a `project_member` role on the valid project (the seed
  grants Tama one) so he can contrast valid-vs-no-access from the same login.

## Scenario → rows

| Scenario | Deployment UUID | Org | Owner | Test user sees it? |
|----------|-----------------|-----|-------|--------------------|
| **Valid** (night skinks / blanks) | `7785fabb-e00e-4da2-aed6-a0fb906e6d79` | General `b0…001` | `a0…001` | ✅ (tui, or any member with a project role) |
| **Valid — diverse content** (cats & birds) | `e005a4ab-a287-4efe-9878-56568f5c30bb` | General `b0…001` | `a0…001` | ✅ — best for exercising SpeciesNet (recognisable animals) |
| **No access** | `242025df-5d55-47d6-b245-e1690ef44126` | `b0…003` (Tama not a member) | `a0…004` | ❌ for Tama `a0…002` → `no_access` |
| **Not found** | `08702e50-f7c6-499f-8b9d-3ecf9cfe050a` | — | — | ❌ (no row) → `not_found` |
| **No deployment ID** | _(none in EXIF)_ | — | — | n/a — images don't bind |

> **`e005a4ab` (cats & birds)** is the most useful for AI-pipeline testing — unlike the skink/blank
> night frames, these are recognisable colour-subject animals SpeciesNet can actually classify. Seed
> it exactly like the valid `7785fabb` row below (General org, owner `a0…001`), with deployment id
> `e005a4ab-a287-4efe-9878-56568f5c30bb`. 11 JPG + 11 BMP frames.

> The `00000000/` and top-level `IMAGES.000/` folders carry **no** `Deployment_ID`; they exercise
> the unconfigured-camera path and need no seed.

## SQL sketch (idempotent)

```sql
-- access_test_deployments.sql — applied after data.sql + fixtures.generated.sql
DO $$
BEGIN
  -- ── VALID: General org, alice owns; grant a non-admin a project role too ──
  -- A camera belongs to no organisation (ww-backend#346): the project decides who sees the deployment.
  INSERT INTO devices (id, name, modified_by, bluetooth_id, device_eui)
  VALUES ('d7785fab-0000-4000-8000-000000000001', 'WW500 — valid test',
          'a0000000-0000-0000-0000-000000000001',
          'BT_TEST_7785', 'EUI_TEST_7785') ON CONFLICT (id) DO NOTHING;

  INSERT INTO projects (id, name, description, organisation_id, created_by, modified_by, is_active)
  VALUES ('a7785fab-0000-4000-8000-000000000001', 'Access Test — Valid',
          'Upload self-binds; the test user has access',
          'b0000000-0000-0000-0000-000000000001',
          'a0000000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000001', true)
  ON CONFLICT (id) DO NOTHING;

  INSERT INTO deployments (id, project_id, device_id, name, location_name, deployment_status_id,
          deployment_start, setup_by, modified_by)
  VALUES ('7785fabb-e00e-4da2-aed6-a0fb906e6d79', 'a7785fab-0000-4000-8000-000000000001',
          'd7785fab-0000-4000-8000-000000000001', 'Valid Access Test', 'Test Location', 2,
          '2026-06-12T00:00:00Z', 'a0000000-0000-0000-0000-000000000001',
          'a0000000-0000-0000-0000-000000000001') ON CONFLICT (id) DO NOTHING;

  -- Optional: let non-admin Tama see the valid one (so he contrasts valid vs no-access).
  IF NOT EXISTS (SELECT 1 FROM user_roles WHERE user_id = 'a0000000-0000-0000-0000-000000000002'
        AND scope_type = 'project' AND scope_id = 'a7785fab-0000-4000-8000-000000000001'
        AND deleted_at IS NULL) THEN
    INSERT INTO user_roles (user_id, role, scope_type, scope_id, granted_by, modified_by, is_active)
    VALUES ('a0000000-0000-0000-0000-000000000002', 'project_member', 'project',
            'a7785fab-0000-4000-8000-000000000001', 'a0000000-0000-0000-0000-000000000001',
            'a0000000-0000-0000-0000-000000000001', true);
  END IF;

  -- ── NO ACCESS: org b0…003, owner a0…004; Tama gets NO role here ──
  -- MUST be b0…003, NOT b0…002: Tama is a member of both b0…001 and b0…002, so seeding this in
  -- b0…002 would silently produce a *valid* fixture and the no-access test would pass vacuously.
  INSERT INTO devices (id, name, modified_by, bluetooth_id, device_eui)
  VALUES ('d242025d-0000-4000-8000-000000000003', 'WW500 — no-access test',
          'a0000000-0000-0000-0000-000000000004',
          'BT_TEST_2420', 'EUI_TEST_2420') ON CONFLICT (id) DO NOTHING;

  INSERT INTO projects (id, name, description, organisation_id, created_by, modified_by, is_active)
  VALUES ('a242025d-0000-4000-8000-000000000003', 'Access Test — No Access',
          'Different org; the non-admin test user is not a member',
          'b0000000-0000-0000-0000-000000000003',
          'a0000000-0000-0000-0000-000000000004', 'a0000000-0000-0000-0000-000000000004', true)
  ON CONFLICT (id) DO NOTHING;

  INSERT INTO deployments (id, project_id, device_id, name, location_name, deployment_status_id,
          deployment_start, setup_by, modified_by)
  VALUES ('242025df-5d55-47d6-b245-e1690ef44126', 'a242025d-0000-4000-8000-000000000003',
          'd242025d-0000-4000-8000-000000000003', 'No-Access Test', 'Test Location', 2,
          '2026-06-12T00:00:00Z', 'a0000000-0000-0000-0000-000000000004',
          'a0000000-0000-0000-0000-000000000004') ON CONFLICT (id) DO NOTHING;

  -- NOT FOUND (08702e50-…): intentionally no rows.
END $$;
```

> Verify `a0000000-…-000004` exists and owns org `b0…003` (check
> `USER-CREDENTIALS-REFERENCE.md`). If not, use any user with **no** membership in `b0…001`/`b0…002`.
> Confirm `deployment_status_id = 2` ("ended") is the value you want — ended deployments are the normal
> "ready to upload SD card" state.

## BMP + JPEG in the fixtures

The `7785FABB/` (valid) and `242025DF/` (no-access) folders now contain both the device **JPG** frames
**and** the raw **`.BMP`** frames (grayscale GRAY8). With `FF_BMP_INGEST_ENABLED` on (default in
`docker-compose.yml`), the upload route re-compresses each BMP to JPEG (`BMP_JPEG_QUALITY`, 90) and
ingests it as its **own** media row — the device alternates one format per frame, so a BMP and a JPG
are *different* captures, not duplicates (see
[bmp-ingestion-analysis.md](./bmp-ingestion-analysis.md)).

This matters for the seed: **BMP frames carry no EXIF**, so they bind **only** via the folder prefix
(`MEDIA/<8-hex>/`) → the seeded deployment, and get their timestamp from the hex filename. So the seed
rows above are exactly what makes the BMP frames resolve too — no extra rows needed. The valid /
no-access scenarios therefore exercise **both** the EXIF-bound (JPG) and folder-bound (BMP) paths
against the same deployment.

## Acceptance test

On a fresh dev DB (with `FF_BMP_INGEST_ENABLED=true`):
1. As **tui** → upload `MEDIA/7785FABB/` → both JPG and BMP-derived frames bind, pipeline runs.
   Upload `MEDIA/08702E50/` → `not_found` banner.
2. As **Tama** → upload `MEDIA/242025DF/` → `no_access` banner (for both JPG and BMP frames).
3. As anyone → upload `MEDIA/00000000/` → images don't bind (no `Deployment_ID`, no folder match).
4. Confirm BMP-derived rows are stored as `image/jpeg` (re-compressed), not raw BMP.
