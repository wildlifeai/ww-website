# The database, and what this repo does not own

This repository consumes a database it does not own. These are the ownership boundaries and
what else breaks when you cross one.

## Database Ownership Invariant

The website does not own the database schema.

All database schema changes must originate in:

```text
ww-backend/supabase/schemas/
```

Never:

* Create tables from this repository
* Modify columns from this repository
* Create database functions from this repository
* Treat this repository as the source of truth for schema

If a schema change is required:

1. File an issue in `ww-backend` with the exact change (columns, constraints, grants,
   `push_changes`), why, and when it is done. Website work does not edit `ww-backend`'s code,
   docs or skills; its maintainers make the change through their schema workflow
2. Update this repository only after the change is on `dev`

**No live database is changed by hand, from here or anywhere.** Dev and staging, which serves
wildlifewatcher.ai, change only through ww-backend migrations, merged to its `dev` first and then
promoted. That includes repairing drift, such as a GRANT the schema declares but production
lacks (ww-backend#247): the repair is a ww-backend migration, never SQL pasted into a project's
editor. When diagnosing production, hand the maintainer read-only SELECTs only.


# 2. Repository Ecosystem Awareness

Before making changes, consider downstream impacts.

| System                            | Relationship                                 |
| --------------------------------- | -------------------------------------------- |
| `ww-website`                      | Website frontend and backend                 |
| `ww-backend`                      | Database schema source of truth              |
| `wildlife-watcher-mobile-app`     | Shares the same Supabase database            |
| `Seeed_Grove_Vision_AI_Module_V2` | Owns EXIF output and LoRaWAN payload formats |
| `ww-hardware`                     | Owns CONFIG.TXT firmware expectations        |

---

# 3. Cross-Repository Ownership Rules

Before changing any shared contract, identify the owning repository.

| Concern                               | Owner             |
| ------------------------------------- | ----------------- |
| Database schema                       | `ww-backend`      |
| EXIF metadata format                  | Firmware          |
| LoRaWAN payload format                | Firmware          |
| CONFIG.TXT structure                  | `ww-hardware`     |
| Shared storage usage                  | Backend ecosystem |
| Shared deployment/device/project data | Backend ecosystem |

Do not modify externally-owned contracts without coordination.
