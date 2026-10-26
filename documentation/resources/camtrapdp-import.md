# CamtrapDP Import & Seeding

**Version**: v1.0
**Status**: Active (gated by `FF_CAMTRAPDP_IMPORT_ENABLED`, default on)
**Last Updated**: June 2026

---

## Overview

The Wildlife Watcher website supports importing [CamtrapDP v1.0](https://camtrap-dp.tdwg.org/) data packages — the international standard for camera trap data exchange. This allows researchers to import existing datasets from other platforms and use the WW Map, Reports, and Export features immediately.

### Architecture

```
CamtrapDP ZIP (uploaded or downloaded)
   │
   ├─ parse_zip()      → CamtrapPackage (in-memory)
   ├─ validate_package() → warnings list
   ├─ Automated Taxa Registration
   │    └─ Fetch missing taxa via iNaturalist API
   │    └─ Insert into taxa table
   └─ import_package()  → inserts into Supabase
        ├─ projects     (1 new project, importing user → project admin)
        ├─ devices      (placeholder per unique cameraID)
        ├─ deployments  (CamtrapDP alignment fields + timezone from GPS)
        ├─ media        (public http(s) URLs stored as-is; zip-embedded images
        │                queued for Google Drive upload → file_path patched to gdrive://<id>)
        └─ observations (species IDs + classification provenance, mode-aware)
```

The HTTP entrypoint is `POST /api/camtrapdp/import` (multipart: `file` = the `.zip`,
`annotation_mode` = `final` (default) | `unprocessed`, `run_ai` = `false` (default) | `true`).
`run_ai=true` additionally runs SpeciesNet + Wildlife Brain over the image-backed imported deployments
and returns an `ai_job_id`. The importing user's **primary organisation** (first `user_roles` org
membership) owns the new project.

### Automated Taxa Registration

During the CamtrapDP import, the system will automatically scan all observations for new `scientificName` values. Any species not already present in the local `taxa` table will be automatically registered:
1. The scientific name is used to query the iNaturalist API (`api.inaturalist.org`).
2. The full taxonomy lineage (kingdom, family, etc.) and `inat_taxon_id` are fetched.
3. The new taxon is inserted into the `taxa` table with a unique `inat_taxon_id` constraint.
4. If a name cannot be resolved against iNaturalist, a warning is logged, and the observation is imported without formal taxonomy linking.

---

## Domain Module

**Path**: `backend/app/domain/camtrapdp.py`

### Key Functions

| Function | Description |
|----------|-------------|
| `parse_zip(zip_bytes)` | Extracts `datapackage.json`, `deployments.csv`, `media.csv`, `observations.csv` from a ZIP file |
| `validate_package(pkg)` | Light validation — returns warnings for missing GPS, missing columns, etc. |
| `import_package(pkg, user_id, org_id, svc)` | Inserts all data into the WW database using a service-role client |

> Each imported deployment also gets its **`timezone`** resolved from `latitude`/`longitude` via
> `resolve_timezone` (timezonefinder), so capture times display in local time. Media `timestamp`
> stays UTC — see [Timezones & capture time](../onboarding/03-DATA-AND-SYNC.md#timezones--capture-time).

### CamtrapDP → WW Vocabulary Mapping

CamtrapDP v1.0 uses a broader controlled vocabulary than the WW check constraints. The import domain maps values automatically:

#### `baitUse` mapping

| CamtrapDP value | WW value | Notes |
|-----------------|----------|-------|
| `none` | `none` | Direct match |
| `scent`, `food`, `visual`, `acoustic` | Same | Direct match |
| `false` | `none` | Boolean-style (common in MICA datasets) |
| `true` | `other` | Boolean-style |
| *(unknown)* | `other` | Fallback |

#### `featureType` mapping

| CamtrapDP value | WW value | Notes |
|-----------------|----------|-------|
| `roadTrail` | `roadTrail` | Direct match |
| `waterSource`, `burrow`, `nestSite` | Same | Direct match |
| `trailGame`, `trailHiking`, `road` | `roadTrail` | Best-fit mapping |
| `culvert`, `bridge` | `other` | Infrastructure |
| *(unknown)* | `other` | Fallback |

#### `eventID` handling

CamtrapDP eventIDs are short hex strings (e.g. `"962dff14"`) but the WW `observations.event_id` column is `uuid`. The import converts them to deterministic UUIDs using `uuid5(NAMESPACE_DNS, eventID)` so:
- The same source eventID always maps to the same UUID
- Observations are correctly grouped by event

---

## Seed Script

**Path**: `scripts/seed_camtrapdp_example.py`

Downloads the official [MICA example dataset](https://camtrap-dp.tdwg.org/example/) (Belgian/Netherlands camera trap data from INBO, released under CC0) and imports it into a target Supabase instance.

### Usage

```bash
# Against local dev
python scripts/seed_camtrapdp_example.py \
    --supabase-url http://localhost:54321 \
    --service-role-key <local-service-role-key> \
    --user-id a0000000-0000-0000-0000-000000000002

# Against dev cloud
python scripts/seed_camtrapdp_example.py \
    --supabase-url https://qegeovogqxiouqbrxmnh.supabase.co \
    --service-role-key <dev-service-role-key> \
    --user-id a0000000-0000-0000-0000-000000000002

# Using a local ZIP (skip GitHub download)
python scripts/seed_camtrapdp_example.py \
    --local-zip ~/Downloads/mica-example.zip \
    --user-id a0000000-0000-0000-0000-000000000002
```

### Parameters

| Flag | Default | Description |
|------|---------|-------------|
| `--supabase-url` | `SUPABASE_URL` env or `http://localhost:54321` | Target Supabase URL |
| `--service-role-key` | `SUPABASE_SERVICE_ROLE_KEY` env | Service role key (required) |
| `--user-id` | see `scripts/seed_camtrapdp_example.py` | UUID of the user who will own the imported project. The examples above pass `a0000000-0000-0000-0000-000000000002` (`tama@ww.org`) — pass it explicitly rather than relying on the default |
| `--local-zip` | *(none)* | Path to a local CamtrapDP ZIP (skips download) |

### Recommended Test Users

| User | UUID | Role | Use Case |
|------|------|------|----------|
| `tama@ww.org` | `a0000000-0000-0000-0000-000000000002` | Org Manager (General) | Default for dev seeding |
| `tui@ww.org` | `a0000000-0000-0000-0000-000000000001` | ww_admin | Full platform access |

### What Gets Imported (MICA Example)

| Entity | Count | Description |
|--------|-------|-------------|
| **Project** | 1 | "Sample from: MICA - Muskrat and coypu camera trap observations in Belgium, the Netherlands and Germany" |
| **Devices** | 4 | Placeholder devices named `[imported] <cameraID>` |
| **Deployments** | 4 | Belgian/Netherlands locations with GPS coordinates |
| **Media** | 423 | Image references with timestamps |
| **Observations** | 549 | Species: Vulpes vulpes, Rattus norvegicus, Anas platyrhynchos, etc. |

### Prerequisites

```bash
pip install supabase requests
```

The script imports from `backend/app/domain/camtrapdp.py`, so it must be run from the `ww-website` root directory.

---

## Cleanup Script

**Path**: `scripts/_cleanup_mica.py`

Removes all MICA-related data (projects, deployments, media, observations, devices) from the target database. Useful when re-running the seed after import code changes.

```bash
python scripts/_cleanup_mica.py
```

> **Note**: This script uses hardcoded bluetooth_id values from the MICA example. It will not affect production data or non-MICA imports.

---

## Integration with Backend

The CamtrapDP import domain depends on the backend database schema. Key tables and constraints:

| Backend Table | Required Columns | Notes |
|---------------|-----------------|-------|
| `deployments` | `setup_by`, `project_id`, `device_id`, `name`, `deployment_start`, `location_name` | `setup_by` is set to the importing user |
| `devices` | `bluetooth_id` (UNIQUE, NOT NULL) | Placeholder devices use `uuid5(DNS, "imported-<organisation id>-<name>")` and no `organisation_id` (ww-backend#346) |
| `media` | `deployment_id`, `file_name`, `file_path` | Public `http(s)` URLs are stored as-is; zip-embedded images are uploaded to Google Drive and `file_path` is patched to `gdrive://<id>` so the resolver can serve thumbnails. |
| `observations` | `deployment_id`, `event_id`, `source_type`, `review_status` | `event_id` via `uuid5`; provenance mapped from `classificationMethod` — see **Annotation mode** below. |

### Annotation mode & provenance

The import UI asks how to treat the package (`annotation_mode`):

- **`final`** (default) — the package is a finished dataset. Imported labels keep correct provenance
  (`classificationMethod=machine` → `source_type=ai`/`review_status=ai_reviewed`; `human` →
  `human`/`human_reviewed`; unspecified → trusted as `human_reviewed`), and **media with no
  observation are seeded a reviewed `blank` observation** ("confirmed empty — no animals"), so they
  show a green ✓ *Empty* instead of a red ✕.
- **`unprocessed`** — the images still need annotating; media with no observation are left bare so
  they surface as work to do.

> This replaced the old behaviour where every imported observation was hardcoded
> `source_type='imported'` / `review_status='unreviewed'`, which made even correctly-annotated and
> empty images render as red ✕ "No label".

### Check Constraints

The `deployments` table enforces check constraints on enum columns. The import domain maps CamtrapDP vocabulary values to pass these constraints:

- `bait_use`: `none | scent | food | visual | acoustic | other`
- `feature_type`: `roadTrail | waterSource | burrow | nestSite | other`
- `observation_type`: `animal | human | vehicle | blank | unknown`
- `life_stage`: `adult | subadult | juvenile | hatchling | unknown`
- `sex`: `male | female | unknown`
- `classification_method`: `human | machine`

---

## Troubleshooting

**"null value in column setup_by"**: The `setup_by` column is required on deployments. Ensure the `--user-id` flag points to a valid `auth.users` UUID.

**"duplicate key value violates unique constraint devices_bluetooth_id_key"**: Previous import data exists. Run `_cleanup_mica.py` first to remove orphan devices.

**"violates check constraint deployments_bait_use_check"**: The CamtrapDP package contains a `baitUse` value not in the WW vocabulary. Add it to the `_BAIT_USE_MAP` in `camtrapdp.py`.

**"Could not find the 'X' column in the schema cache"**: The import code is sending a column name that doesn't match the DB schema. Check the column names in `camtrapdp.py` against the backend schema definition.
