# Dual-AI (Camera AI + Cloud AI) — Production Rollout Runbook

> How to promote the **dual-layer AI** feature — the camera's on-device (edge)
> predictions appearing as observations beside the Cloud AI pipeline — from `dev`
> to **staging** and **production**, what each step touches, and what gates real
> end-to-end value. Companion to [embedded-model-lifecycle](./embedded-model-lifecycle.md)
> (the how-it-works map) and [AI-ARCHITECTURE](./AI-ARCHITECTURE.md) (naming + contracts).

Repos: **backend** (`ww-backend`, canonical schema), **website** (`ww-website`,
API + worker + frontend), **firmware** (`Seeed_Grove_Vision_AI_Module_V2`),
**mobile** (`ww-mobile-app`).

---

## 1. What this feature is (one paragraph)

The WW500 camera runs a small on-device model and writes its per-class NN scores
into each JPEG's EXIF `UserComment` (`"rat: 87; not rat: 13; "`). On upload,
`domain/exif.py` parses those into `media.exif_metadata.user_comment_fields`, and
`domain/edge_reflection.py` turns them into `observations` tagged
**`ai_origin='edge'`** — mapped to taxa via the project model's `label_map`,
skipping background classes and sub-threshold scores. These **Camera AI** rows sit
beside the **Cloud AI** (SpeciesNet) rows on the same media, distinguished by
`observations.ai_origin` and surfaced with a 📟 badge in the annotation panel.

---

## 2. How the environments promote (the key to low risk)

The two repos promote **differently**, and that asymmetry is why this is safe:

| Repo | dev | staging / production |
|---|---|---|
| **ww-backend** (schema) | **destructive reset + full reseed** (`db reset --no-seed` then `seed.sql` + `dev/data.sql` + fixtures), which is why the demo Rat model + deployment exist only on dev | **schema-only `supabase db push`**: additive migrations, **no reset, no reseed** ([deploy_cloud_projects.yml](https://github.com/wildlifeai/ww-backend/blob/main/.github/workflows/deploy_cloud_projects.yml)) |
| **ww-website** (app) | merge to `dev`, then deploy the AWS `ww-dev` stack by hand or with `deploy-aws.yml` ([deployment guide](deployment-guide.md#aws-deploy-awsyml)) | a `dev` to `main` release → build + roll `ww-backend` / `ww-embedding-worker` (`.github/workflows/deploy-backend.yml`; there is no staging branch) |

**Consequences:**
- The `dual_ai_v0` migration (`observations.ai_origin` + `device_alert_rules`) is
  **additive and non-breaking** — a nullable column + a new table. Promoting it to
  production via `db push` is zero-downtime and needs no data migration.
- The demo **seed data never reaches prod** — it lives in `supabase/seeds/dev/data.sql`,
  applied only when `target_env == 'dev'`. Production orgs onboard their **own** edge
  models through the model-management UI (§5).

---

## 3. What's already done (on dev)

- ✅ ww-backend `dual_ai_v0` migration merged (`observations.ai_origin`, `device_alert_rules`) — additive.
- ✅ ww-website edge reflection (`domain/edge_reflection.py`) + 📟 Camera-AI badge merged.
- ✅ `FF_EDGE_REFLECT_ENABLED=true` on dev (SSM `/ww/dev/`).
- ✅ EXIF NUL-byte ingest fix (§6) — a production-blocker found during the dev demo.

---

## 4. The gating dependency (read this before scheduling a prod rollout)

Promoting the **code** is trivial; what makes the feature **actually do something in
production** is upstream of the website entirely:

> **Firmware, no longer a gate (checked 2026-11-26):** the firmware's `main` has
> `USE_PERCENTAGE` on since 2026-07-08 (Seeed commit `fb66bafd`, `cvapp.h`), so a camera
> on that firmware writes `"label: pct%; "` into `UserComment`; the bench run of 2026-09-05
> saw scores in every frame. A camera on older firmware writes none, and reflection does
> nothing for its photos. What the camera's own detection threshold (op 16) means is a
> separate contract: [AI-ARCHITECTURE](./AI-ARCHITECTURE.md#the-contracts-that-must-never-break).

---

## 5. Real edge-model onboarding (prod, not the demo seed)

In production an org gets a working Camera AI model by walking the full lifecycle
(all built on the website — see [ai-model-pipeline](./ai-model-pipeline.md)):

1. **Train & export** an int8-quantized TFLite classifier (Edge Impulse).
2. **Upload & convert** (`POST /api/models/convert` → Vela → `.TFL` + `.TXT`).
3. **Label mapping** (`ModelLabelMapper`) — each output class → *target* (with a taxon)
   or *background*; this is what lets a `rat: 87` score become *Rattus rattus*.
4. **Manifest / Prepare SD card** — `MANIFEST.zip` named `{firmware_model_id}V{version}.TFL`.
5. **Deploy to device** (mobile BLE `loadmodel` OP14+OP15, or SD card) + **assign the
   model to the project** (`projects.model_id`) so reflection knows which `label_map` to use.

The demo shortcut was to *seed* steps 2–5 (a Rat Detection model + project assignment);
prod uses the real UI.

---

## 6. Production bug found + fixed during the demo (EXIF NUL bytes)

EXIF `UserComment` begins with an 8-byte NUL-padded charset code (`ASCII` + 3 NULs) —
exactly what the firmware writes. Postgres `jsonb` rejects the NUL codepoint (SQLSTATE
`22P05`), so storing the parsed dict as `media.exif_metadata` **failed media
registration for every frame that carried a UserComment**. Latent then because devices
did not emit UserComment; they do now (§4). Fixed in `domain/exif.py` (`_strip_nul`
sanitises the parsed dict), which is on `dev` and `main`.

---

## 7. Production rollout checklist

Do these in order. Step 2 is done: `dual_ai_v0` is on ww-backend `main` (checked 2026-11-26).

- [ ] **1. Coordinate the schema change with mobile.** The `ai_origin` column changes
      the generated `database.types.ts` mobile consumes. File the coordination note
      (schema-change template) — the change is non-breaking (additive nullable column).
- [x] **2. Promote the schema to production.** Merge the `dual_ai_v0` migration to
      ww-backend `main` → `supabase db push` applies `ai_origin` + `device_alert_rules`
      additively (no reset, no reseed). Verify: `SELECT ai_origin FROM observations LIMIT 1;`
      resolves and `device_alert_rules` exists.
- [ ] **3. Promote the app to production.** Edge reflection, the badge and the EXIF
      NUL fix are on `main`; a `dev` to `main` release carries anything newer. Confirm
      API + worker roll.
- [ ] **4. Repeat this demo on staging** to validate end-to-end (§8) before prod.
- [ ] **5. Set the feature flag.** The firmware writes percentage scores (§4). Enable
      `FF_EDGE_REFLECT_ENABLED` on **both** the prod API and worker:
      - API: `az containerapp update -n <api> -g WW-AE --set-env-vars FF_EDGE_REFLECT_ENABLED=true`
      - Worker: `az containerapp revision copy -n <worker> -g WW-AE --set-env-vars FF_EDGE_REFLECT_ENABLED=true`
        (use `revision copy`, **not** `update` — a plain `--set-env-vars` on the worker has
        blanked the KEDA scaler query before). Verify the scaler survived:
        `az containerapp show -n <worker> -g WW-AE --query properties.template.scale`.
      - Prefer moving the flag into the deploy env config so it survives future rolls,
        rather than relying on manual `az`.
- [ ] **6. Frontend badge** ships with the app (no separate step).

---

## 8. Validation (the demo, repeatable on any env)

1. Seed or onboard an edge model with a real `label_map` (target + background) and
   assign it to a project (`projects.model_id`).
2. Stamp a few JPEGs with `UserComment = "rat: 87; not rat: 13; "` (or use real device
   frames). Upload via `POST /api/exif/parse` with
   `upload_to_drive=true`, `run_ai=true`, `assigned_deployment_id=<demo deployment>`.
3. The worker (KEDA-woken on `api_jobs`) runs the Cloud pipeline **and** edge reflection.
4. **Assert:** `observations` for the deployment contain `ai_origin='edge'` rows (one per
   target label at/above threshold) *beside* the `ai_origin='cloud'` SpeciesNet rows, and
   the annotation panel shows 📟 Camera AI next to ☁ Cloud AI.

> Gotcha checklist if no edge rows appear: `FF_EDGE_REFLECT_ENABLED` on the **worker**
> (not just the API); the project has a `model_id` with a non-empty `label_map`;
> `media.exif_metadata.user_comment_fields` is populated (needs the §6 NUL fix);
> the upload used `upload_to_drive=true` (otherwise nothing is persisted).

---

## 9. Rollback

- **Flag:** set `FF_EDGE_REFLECT_ENABLED=false` (API + worker) — reflection stops; no
  edge rows are written. Existing edge rows are inert (they're normal `ai_reviewed`
  observations and can be filtered by `ai_origin`).
- **Schema:** the additive column/table are harmless if unused; no rollback needed.
  Do **not** drop `ai_origin` while any edge rows exist.

---

## 10. Not in scope of this rollout

- **`device_alert_rules` LoRaWAN alerting** — the table landed with `dual_ai_v0`, but
  compiling alert rules into the device manifest and executing instant/digest/backoff
  on the Nordic firmware is **not built**. Separate effort.
- **Golden-set score parity (LM-3)** — verifying on-device scores against the physical
  device is the parity harness's job, tracked separately.
