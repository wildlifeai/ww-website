# The database, and what this repo does not own

This repository consumes a database it does not own. These are the ownership boundaries and
what else breaks when you cross one.

## Database Ownership Invariant

The rule is in [AGENTS.md](../../../AGENTS.md#non-negotiables): every schema change originates in
`ww-backend/supabase/schemas/`. If a schema change is required:

1. File an issue in `ww-backend` with the exact change (columns, constraints, grants,
   `push_changes`), why, and when it is done. Website work does not edit `ww-backend`'s code,
   docs or skills; its maintainers make the change through their schema workflow
2. Update this repository only after the change is on `dev`

**No live database is changed by hand, from here or anywhere.** Dev and staging, which serves
wildlifewatcher.ai, change only through ww-backend migrations, merged to its `dev` first and then
promoted. That includes repairing drift, such as a GRANT the schema declares but production
lacks (ww-backend#247): the repair is a ww-backend migration, never SQL pasted into a project's
editor. When diagnosing production, hand the maintainer read-only SELECTs only.


## Repository ecosystem

Before making changes, consider downstream impacts.

| System                            | Relationship                                 |
| --------------------------------- | -------------------------------------------- |
| `ww-website`                      | Website frontend and backend                 |
| `ww-backend`                      | Database schema source of truth; decodes LoRaWAN uplinks (`lorawan-ingest`) |
| `wildlife-watcher-mobile-app`     | Shares the same Supabase database            |
| `Seeed_Grove_Vision_AI_Module_V2` | Himax firmware: owns EXIF output             |
| `ww-hardware`                     | nRF52 firmware: builds the LoRaWAN uplink, owns CONFIG.TXT expectations |

---

## Cross-repository ownership

Before changing any shared contract, identify the owning repository.

| Concern                               | Owner             |
| ------------------------------------- | ----------------- |
| Database schema                       | `ww-backend`      |
| EXIF metadata format                  | Seeed firmware    |
| LoRaWAN payload format                | `ww-hardware` builds it (`app_ww.c`; ww-hardware PR #81 adds the tag-value message), `ww-backend`'s `lorawan-ingest` decodes it into `lorawan_parsed_messages` |
| CONFIG.TXT structure                  | `ww-hardware`     |
| Live preview console protocol         | Seeed firmware (`_Documentation/live_preview.md`, shared with `_Tools/live_view.py`) |
| Shared storage usage                  | Backend ecosystem |
| Shared deployment/device/project data | Backend ecosystem |

Do not modify externally-owned contracts without coordination.
