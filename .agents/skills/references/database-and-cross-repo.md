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

1. Make the change in `ww-backend`
2. Follow the backend schema workflow
3. Update this repository only after the schema exists


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
