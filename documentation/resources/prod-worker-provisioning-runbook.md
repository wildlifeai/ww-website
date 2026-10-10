# Runbook — Provision the **production** GPU ML worker

> **Status:** 📋 Ready to run *when prod has real traffic*. Dev is already GPU-live
> ([cloud-infrastructure.md](cloud-infrastructure.md)); this duplicates that setup for prod.
> **Don't run early** — a prod worker that never scales to zero is an idle T4 burning money.

## Why this is needed
Production today is **API-only**: `ww-backend` (the lean `--target api` image, *no ML deps*) with **no
worker and no Redis broker**. So prod can't run species detection at all. This runbook stands up the prod
worker stack. The **GPU itself needs no new setup** — the `gpu-t4` serverless-T4 workload profile lives on
the shared `ww-env` environment, so the prod worker reuses it (no extra quota, no new profile).

## Prerequisites
- The dev GPU setup working (proves the profile + image). Worker image: `wwregistry.azurecr.io/ww-backend-worker` (CUDA-ready). Use a **prod tag** (e.g. `latest`), not `dev-latest`.
- **Cost guardrails (learned on dev, 2026-07-09):** a job left in `status='processing'` pins the
  GPU awake for the whole KEDA query window. Before provisioning prod:
  1. Confirm the **ARQ terminal-status fix** is merged (the worker must write
     `completed`/`failed` to `api_jobs` — on dev the row froze at `processing`/progress 1.0 and
     held the T4 up for ~1 h per 84 s job).
  2. **The Azure Monitor alert is the primary guard** (below) — it catches a stuck GPU without
     any risk of killing live work. Add it first.
  3. Shortening the KEDA query window is secondary and has a **sharp edge**: the annotate job only
     refreshes `updated_at` at the *start of each pipeline step* (`_on_step` in
     `auto_annotate_deployments`), NOT within a step. A single long step on a large deployment
     (e.g. SpeciesNet over thousands of images) can exceed the window with no heartbeat, so KEDA
     scales the worker to zero **mid-inference and kills the job**. So:
     - **Dev** (small test deployments) runs a **15-minute** window safely — applied 2026-07-09.
     - **Prod** (real, potentially huge deployments) must either keep a generous window
       (≥ the reaper's 60 min) OR add an intra-step heartbeat first; do not blanket-copy 15 min.
  4. Create an **Azure Monitor alert** on the worker: `Replicas >= 1` sustained > 30 min → email
     (dev: action group `ww-gpu-alerts`, alert `ww-gpu-worker-stuck-dev`, created 2026-07-09).
  5. Keep `--max-replicas 1` unless the queue demonstrably needs more.
- Prod Supabase project: **`nuhwmubvygxyddkycmpa`** — have its **service-role key**, **anon key**, and **DB password** (for the pooler).
- The Google service-account JSON + Azure Storage connection string (can be the same as dev, or prod-specific).
- `az` logged in; default RG `WW-AE`, region `australiaeast`.

## Convention
All prod names mirror dev without the `-dev` suffix: **`ww-redis`**, **`ww-embedding-worker`**. Everything in `WW-AE` / `ww-env`.

---

## 1 · Prod Redis broker (ARQ)
KEDA can't reach an internal Redis, but the **worker/API** reach it app-to-app by short name (as on dev).
```bash
az containerapp create -n ww-redis -g WW-AE --environment ww-env \
  --image redis:7-alpine \
  --min-replicas 1 --max-replicas 1 \
  --cpu 0.5 --memory 1Gi \
  --transport tcp --target-port 6379 --exposed-port 6379 --ingress internal
```
Reachable from other apps as **`redis://ww-redis:6379`** (short name — the `.internal.*` FQDN times out for TCP).

## 2 · Prod worker secrets
```bash
az containerapp secret set -n ww-embedding-worker -g WW-AE --secrets \
  acr-pw="<ACR admin password>" \
  supabase-service-key="<PROD service-role key>" \
  supabase-anon-key="<PROD anon key>" \
  azure-storage-conn="<storage connection string>" \
  google-sa-json="<service-account JSON>" \
  hf-token="<HuggingFace token with gated access to facebook/dinov3-*>" \
  pg-conn="postgres.nuhwmubvygxyddkycmpa:<PROD DB password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require"
```
*(If creating the app first, set secrets in `create` via `--secrets`. `pg-conn` must be the **shared Session pooler** — IPv4, **port 5432** — not the direct/dedicated host, which is IPv6-only and unreachable by KEDA. Use **Session mode (:5432), not Transaction (:6543)**: the KEDA Postgres scaler reuses prepared statements, which Transaction pooling rejects with `42P05` — this broke the **dev** scaler until we moved to :5432 (2026-07-09). `pg-conn` is used only by the KEDA scale rule here; the worker itself talks to Supabase via the service-role client. `hf-token` is **required** — DINOv3 is a gated HF model; without it the Wildlife Brain embed 401s; the dev deploy syncs it from the `HF_TOKEN` GitHub org secret, so mirror that or set it here.)*

## 3 · Prod worker on the GPU profile
```bash
az containerapp create -n ww-embedding-worker -g WW-AE --environment ww-env \
  --image wwregistry.azurecr.io/ww-backend-worker:latest \
  --workload-profile-name gpu-t4 \
  --registry-server wwregistry.azurecr.io --registry-username wwregistry --registry-password "<ACR admin password>" \
  --min-replicas 0 --max-replicas 1 \
  --cpu 4 --memory 16Gi \
  --env-vars \
    SUPABASE_URL="https://nuhwmubvygxyddkycmpa.supabase.co" \
    HF_TOKEN=secretref:hf-token \
    SUPABASE_SERVICE_ROLE_KEY=secretref:supabase-service-key \
    SUPABASE_ANON_KEY=secretref:supabase-anon-key \
    AZURE_STORAGE_CONNECTION_STRING=secretref:azure-storage-conn \
    GOOGLE_SERVICE_ACCOUNT_JSON=secretref:google-sa-json \
    REDIS_URL="redis://ww-redis:6379" \
    GOOGLE_DRIVE_ENABLED=true GOOGLE_DRIVE_FOLDER_ID="<prod Drive root>" \
    FF_ML_ENABLED=true FF_PIPELINE_ENABLED=true FF_SPECIESNET_ENABLED=true \
    FF_BIOCLIP_ENABLED=true FF_PER_CROP_CLASSIFY_ENABLED=true FF_MEDIA_REGISTRY_ENABLED=true \
    FF_WILDLIFE_BRAIN_ENABLED=true SPECIESNET_RUN_MODE=single_thread LOG_LEVEL=info \
    EMBEDDING_DEVICE=cuda BIOCLIP_DEVICE=cuda
```
`FF_ML_ENABLED` **must** be true (else `build_pipeline_steps()` returns `[]` → no AI). `GOOGLE_SERVICE_ACCOUNT_JSON` must be raw JSON, not a list-repr (the `-o tsv` array bug corrupts it — set as a secret, don't round-trip via CLI arrays).

> ⚠️ **`--memory 16Gi` is required — not the 8Gi default.** The auto-chained annotate→embed flow holds
> SpeciesNet + BioCLIP + **DINOv3 ViT-H16plus** resident in one process; at 8Gi, loading ViT-H on top
> **OOM-kills the worker (exit 137) mid-embed**, orphaning the embedding run — confirmed on dev
> 2026-07-10, fixed by bumping to 16Gi (`cpu 4 / memory 16Gi` on the `gpu-t4` profile). The dev worker
> now runs 16Gi, and `deploy-backend.yml`'s `revision copy` preserves it across rolls. (Durable
> alternative: run the embed as its own job so the three heavyweight models never coexist.)

> **Erratum (2026-07-26, first prod run):** `--registry-password-secret-ref` is not accepted by
> current az; pass `--registry-password` (az stores it as an auto-named secret). Executed this
> day: `ww-redis`, `ww-embedding-worker` (gpu-t4, 16Gi, 0-1), `REDIS_URL` on `ww-backend`,
> `ww-gpu-worker-stuck` alert. Drive az from **PowerShell** for `--scopes /subscriptions/...`
> (git-bash rewrites the leading slash as a path).

## 4 · KEDA scale-to-zero on prod `api_jobs`
> ⚠️ **Do NOT use `az containerapp update --scale-rule-metadata`** — the CLI silently drops the SQL (spaces/quotes), leaving `query` empty → with `min=0` the worker never wakes. Set it in the **Portal** or a full `--yaml`.

**Portal:** `ww-embedding-worker` → **Scale and replicas** → add rule `pg-pending`, type **PostgreSQL**, auth `connection` → secret `pg-conn`, metadata:
```
query            = SELECT count(*) FROM api_jobs WHERE status IN ('queued','processing') AND updated_at > now() - interval '1 hour'
targetQueryValue = 1
```
Verify: `az containerapp show -n ww-embedding-worker -g WW-AE --query "properties.template.scale.rules[0].custom.metadata"`

## 5 · Wire the prod API to offload AI
```bash
az containerapp update -n ww-backend -g WW-AE \
  --set-env-vars REDIS_URL="redis://ww-redis:6379" FF_ML_ENABLED=true FF_PIPELINE_ENABLED=true
```
With `REDIS_URL` set, the upload job enqueues `annotate_deployments_job` to the prod worker (see `jobs/definitions.py`); without it, AI would try to run in-process on the lean API image and fail.

## Verification
```bash
# GPU present in the worker (torch sees CUDA):
az containerapp exec -n ww-embedding-worker -g WW-AE \
  --command "python -c \"import torch;print('cuda?',torch.cuda.is_available())\""     # → True

# scaler query populated (not empty):
az containerapp show -n ww-embedding-worker -g WW-AE --query "properties.template.scale.rules[0].custom.metadata.query" -o tsv

# idle → 0 replicas:
az containerapp replica list -n ww-embedding-worker -g WW-AE -o table                 # empty when no jobs
```
Then run a **real prod upload** → within ~30 s a replica should appear (KEDA woke it), the pipeline logs should show `speciesnet_step_complete` at ~1–2 s/image, and it should return to 0 after the 300 s cooldown.

## Cost / rollback
- **Cost:** a prod T4 is per-second **while running**. Confirm scale-to-zero (step-Verify) — a worker stuck at ≥1 replica is an idle GPU. With dev + prod both active you can have **two concurrent T4s**.
- **Rollback / pause:** `az containerapp update -n ww-embedding-worker -g WW-AE --min-replicas 0 --max-replicas 0` (hard-stop, no AI), or delete `ww-embedding-worker` + `ww-redis` to remove the prod AI stack entirely. The `gpu-t4` profile stays on `ww-env` for dev.

## Notes
- Dev + prod workers **share `ww-env`**; isolation is at the app + Supabase-project level (prod worker → prod Supabase, prod `pg-conn`), not the environment. Keep the two `pg-conn` secrets pointed at the correct projects — crossing them would process prod jobs against dev data.
- CI redeploy (`deploy-backend.yml`) must target RG `WW-AE` and push to `wwregistry.azurecr.io` before a `:latest` prod image exists.
