# Report — Schema & seed items for the `ww-backend` team

> **Status:** 📋 Open hand-off, checked 2026-11-26 against ww-backend `dev` and `main`: items 3, 4 and
> 8 still hold and are not yet filed as ww-backend issues, which the board's rule 1 requires; item 1
> was wrong (the tables exist) and item 7 is done. Raised 2026-07-28 from a full audit of `ww-website/documentation`.
> **Audience:** `ww-backend` maintainers (schema, RLS/GRANTs, seeds).
> **Nothing here needs a ww-website change** unless noted; the website-side doc fixes are already done.

Every claim below was verified against the `ww-backend` working tree, not inferred from docs. File and
line references are to `ww-backend` unless prefixed `ww-website/`.

---

## 1. iNaturalist tables are declared but were never migrated — the feature cannot work

**Wrong, corrected 2026-11-26.** The three tables are in ww-backend's squashed baseline,
`supabase/migrations/20260610030000_squashed_baseline.sql`, so they exist wherever it ran; dev has
them (checked 25 Oct 2026). Nothing to do. The finding as raised:

**The finding.** Three tables exist under `supabase/schemas/`:

```
supabase/schemas/public/tables/51_inat_observations.sql
supabase/schemas/public/tables/52_inat_observation_media.sql
supabase/schemas/public/tables/53_inat_tokens.sql
```

There is **no corresponding migration** in `supabase/migrations/` (`ls supabase/migrations | grep -i inat`
returns nothing). Declarative schema files alone don't create anything — so these tables do not exist in
dev, staging or production.

**Why it matters.** `ww-website` shipped the code that reads and writes them on **2026-06-08** — publish
(`domain/inaturalist_publish.py`), sync-back (`domain/inaturalist_sync.py`), the `POST /api/inat/publish`
and `/sync` endpoints, and the thumbnail badge. That's ~7 weeks of shipped-but-dead code. The website
degrades gracefully (`MediaBrowser` does a tolerant lookup and shows no badges rather than erroring), which
is exactly why nobody noticed. `FF_INAT_ENABLED` is `false` by default, so the blast radius is contained —
but the feature cannot be switched on in any environment until this lands.

**Action.** In `ww-backend`: `DB_AGENT_MODE=1 npm run db:change inat_observations`, review the generated
migration, commit schema + generated artifacts, and notify mobile via the type-sync guide (the generated
`database.types.ts` changes). This is step 1 of the go-live checklist in
[inaturalist-integration.md](./inaturalist-integration.md#8-go-live-checklist) and the only one that isn't
a website config change.

---

## 2. Two canonical docs moved; every cross-repo link to them was broken

`USER-CREDENTIALS-REFERENCE.md` and `CLOUD_SEEDING.md` now live under `documentation/resources/`. Six
`ww-website` docs still pointed at their old homes:

| Doc | Was linked as | Actually at |
|---|---|---|
| `USER-CREDENTIALS-REFERENCE.md` | `ww-backend/supabase/seeds/…` | `ww-backend/documentation/resources/…` |
| `CLOUD_SEEDING.md` | `ww-backend/supabase/…` | `ww-backend/documentation/resources/…` |

**Already fixed on the website side.** Flagging it because those links just became *more* load-bearing:
`ww-website` docs used to duplicate the seed counts ("17 users, 4 orgs, 5 projects") and had drifted out
of sync with each other, so we deleted the local copies and now defer to
`USER-CREDENTIALS-REFERENCE.md` as the single source of truth.

**Ask.** Two things: (a) keep `USER-CREDENTIALS-REFERENCE.md` current when the seed changes — it is now the
only place those counts exist; (b) when moving a doc that other repos link to, leave a one-line stub at
the old path or grep the sibling repos.

---

## 3. `embedding_runs.qdrant_collection` — a dead vendor's name on a live column

**Done 2026-11-26:** renamed to `vector_space` by ww-backend#396 (#387), with the website half in ww-website#510.

```sql
-- supabase/schemas/public/tables/34b_embedding_runs.sql:27
qdrant_collection text NOT NULL DEFAULT 'media_embeddings',
```

Qdrant was removed from `ww-website` on **2026-07-09** — no `qdrant-client` dependency, no `QDRANT_*`
config, no compose container. The vector store is **pgvector in Supabase**
(`media_embeddings.embedding`, searched via the `match_media_embeddings` RPC). The column above is now
just a **label naming the logical vector space** — the two DINOv3 variants have different dimensions
(384 vits / 1280 vith) so they can't share one, and its values are `media_embeddings` /
`media_embeddings_vits`. Nothing about it touches Qdrant.

**Suggested rename:** `vector_space`. This was anticipated at the time — the GPU-worker spec said "keep
`qdrant_collection` as the *logical* space id, or rename to `vector_space`" — and the rename half never
happened.

**Cost.** One `ww-backend` column rename + 11 references in `ww-website`
(`registries/embedding_registry.py`, `domain/wildlife_brain.py:377,611`, `tests/test_embedding_registry.py`).
Needs to land in lockstep. **Low priority, cosmetic** — but it is the kind of thing that costs a new
engineer twenty minutes and a wrong assumption. Happy to do the website half whenever you want to do the
column; there's no rush from our side.

---

## 4. `access_test_deployments.sql` still calls the seed admin "alice"

```
supabase/seeds/dev/access_test_deployments.sql:22   -- NOTE: alice (a0…001) is a global ww_admin …
supabase/seeds/dev/access_test_deployments.sql:27   -- ═══ VALID — General org (b0…001), owner alice (a0…001) ═══
```

`a0000000-…-000001` is **Tui Smith / `tui@ww.org`** (`supabase/seeds/dev/data.sql:133,162`, and
`USER-CREDENTIALS-REFERENCE.md:190`). "alice" is a persona name from before the seed was renamed to Māori
names. Two comment lines — no behaviour change.

The same stale name had propagated into the `ww-website` spec that this seed file implements; that copy is
now fixed.

---

## 5. The seed password was published in a `ww-website` doc — rotate if it was ever real

`ww-website/documentation/resources/deployment-guide.md` stated *"17 test users (password: `test123`)"*,
directly contradicting `testing-with-seed-users.md`, which says the password comes from
`SEED_USER_PASSWORD` (a GitHub secret) and is "never hardcoded in source".

**Removed from the website docs.** The question for you: **was `test123` ever the actual
`SEED_USER_PASSWORD` on cloud dev?** If so, rotate it in the `dev` GitHub environment (and note
`DEMO_PASSWORD` must be kept equal to it — see `demo-account.md`). If it was only ever a stale local
default, nothing to do; please confirm either way so we can close it.

---

## 6. Verify GRANTs and RLS against the **live** databases, not the schema files

The baseline schema is correct today:

```
supabase/schemas/public/tables/35_observations.sql:123  GRANT SELECT, INSERT, UPDATE ON public.observations TO authenticated;
supabase/schemas/public/tables/46_media_assets.sql:43   GRANT SELECT ON public.media_assets TO authenticated;
```

But **production has diverged from it before, silently and twice in two days** (26 Jul 2026):

- `media_assets` was missing its `SELECT` grant in prod. Because PostgREST aborts the *whole* request when
  an embedded resource fails, **every** Annotations query died with `permission denied` — not a degraded
  view, a blank one.
- A stale `media` RLS predicate reached prod.

A GRANT present in the baseline can still be absent from an environment created before it was added. The
`ww-website` repo has `scripts/parity_audit.py`, which diffs container env, secret names, buckets, auth
providers, and — via a printed SQL query you feed back with `--sql-dev` / `--sql-prod` — **RLS policies,
grants, function bodies (md5), and the realtime publication**.

**Ask.** Run the SQL half against dev + prod and reconcile anything unexplained. The audit is only as good
as the last time someone ran it, and the DB half is yours.

---

## 7. Promote `dual_ai_v0` to production when the firmware milestone lands

`supabase/migrations/20260710084653_dual_ai_v0.sql` (adding `observations.ai_origin` and the
`device_alert_rules` table + policies) is merged and live on dev. It is **additive and non-breaking** — a
nullable column and a new table — so `supabase db push` to prod is zero-downtime with no data migration.

Two notes:

- **Coordinate with mobile first.** `ai_origin` changes the generated `database.types.ts` they consume.
  The change is non-breaking, but it should go through the schema-change template.
- **No rush.** Enabling the feature in prod is gated on a firmware fix (`USE_PERCENTAGE`) that hasn't
  shipped — until then edge reflection is a safe no-op. Pushing the schema early is harmless and takes the
  schema off the critical path. Full sequence:
  [dual-ai-production-rollout.md §7](../resources/dual-ai-production-rollout.md).

**Done, checked 2026-11-26:** `20260710084653_dual_ai_v0.sql` is on ww-backend `main`, and the
firmware fix was not a gate after all: `USE_PERCENTAGE` is on in the firmware's `main` since
2026-07-08 (Seeed commit `fb66bafd`).

---

## 8. Schema work two `ww-website` specs are waiting on

Both verified **not present** in `supabase/`:

| Spec | Needs from ww-backend | State |
|---|---|---|
| [storage-quota-spec](./storage-quota-spec.md) | `media.file_bytes`, `organisation_usage` table, `app_settings` table, the `fn_media_usage_delta` trigger, RLS + SELECT grant | Not started. Website side (enforcement + UI) is blocked on it |
| [decoupled-upload-pipeline-spec](./decoupled-upload-pipeline-spec.md) | `media.backup_status` / `backup_error` / `backup_synced_at` / `staging_blob_id`, the `(deployment_id, file_hash)` unique index, partial index on `backup_status` | Proposal — **not approved yet**, no action needed. Listed so it isn't a surprise if it is |

The storage-quota spec is the one with a concrete design ready to implement; it includes the DDL, the
trigger, the RLS policy and a reconcile query. Neither is urgent from the website side today.

---

## Verified correct — no action needed

Recorded so these don't get re-litigated:

- **`access_test_deployments.sql` seeds the no-access fixture correctly** — org `b0…003`, owner `a0…004`,
  which Tama (`a0…002`) is not a member of. The *`ww-website` spec's SQL sketch* had `b0…002` (an org Tama
  **is** in), which would have made the no-access test pass vacuously. The sketch was wrong; your file was
  always right. Sketch now fixed.
- **`dual_ai_v0` shipped as designed** — `observations.ai_origin` with the `edge`/`cloud` check, plus
  `device_alert_rules` with the documented columns and RLS split (project members read, admins manage).
  The `ww-website` proposal that sketched it called the table `alert_rules`; that doc has been corrected to
  match what shipped.
- **`observations` write grants are in the baseline** — `GRANT SELECT, INSERT, UPDATE … TO authenticated`,
  which is what lets reviewers confirm/correct labels. (Still worth the live check in §6.)

---

## For context: what changed on the `ww-website` side

A documentation audit on 2026-07-27 found 22 contradictions and 9 gaps; all are fixed. The parts that
touch you:

- Website docs no longer duplicate seed counts or the seed password — they defer to
  `USER-CREDENTIALS-REFERENCE.md`.
- `lorawan-webhook-setup.md` is now clearly labelled a **legacy prototype**, with the canonical production
  ingest identified as your `lorawan-ingest` edge function
  (`ww-backend/documentation/resources/LORAWAN_INGEST.md`). Three other docs that cited it as current were
  corrected. **Please confirm** that's the right characterisation — it came from that doc's own header.
- The `ww-backend` repo URL was inconsistent across website docs (`ww-backend` vs
  `wildlife-watcher-backend`); all now use `wildlife-watcher-backend`. Tell us if that's the wrong one.
  (2026-11-26: the repo has since been renamed `ww-backend`, and the website docs use that name.)
  (2026-11-26: the repo has since been renamed `ww-backend`, and the website docs use that name.)
