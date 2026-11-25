# Spec — ARQ GPU Worker infrastructure (Wildlife Watcher AI pipeline)

> **Status:** ✅ **Shipped**, dev 2026-07-03 and production 2026-07-26 (`ww-embedding-worker`); retained
> as the original **design spec / history**, not as an operating procedure. The AWS migration retires
> it at its step 8 ([2026-10_aws-reannz-migration](2026-10_aws-reannz-migration/README.md)). The dev worker `ww-embedding-worker-dev` runs on a **serverless T4 GPU**
> (`gpu-t4` = `Consumption-GPU-NC8as-T4` profile on `ww-env`, `cuda`, scale-to-zero).
>
> **Read the live docs first — parts of this spec were overridden during the build** (§3 Redis and §7
> scale rule in particular; see the note at the end). What actually exists:
> [cloud-infrastructure.md](../resources/cloud-infrastructure.md) · how to stand up prod:
> [prod-worker-provisioning-runbook.md](../resources/prod-worker-provisioning-runbook.md).
> All `--resource-group WW-Website` commands below are **stale** — everything now lives in `WW-AE`
> (`australiaeast`); the old RG was deleted 2026-06-30.

**Goal:** stand up the **ARQ GPU worker** that runs the heavy ML pipeline (SpeciesNet detect, BioCLIP
classify, DINOv3 embeddings) off the lean API. This is the **infrastructure prerequisite** for (a)
turning on AI species detection on the dev/prod backends at all — today both container apps run the
lean `--target api` image with **no ML deps** — and (b) the per-detection classifier in
[per-crop-classification-spec.md](_archive/per-crop-classification-spec.md), which this worker unblocked (shipped).

## Design at a glance

The two-container split — **always-on CPU API + on-demand GPU worker** — is already wired in code.
What's missing is **infra + config**, not implementation:

| Piece | Status today | This spec |
|-------|--------------|-----------|
| `dispatch.enqueue_job` routes ARQ vs in-process on `REDIS_URL` | ✅ [dispatch.py](../../backend/app/jobs/dispatch.py) | provision Redis, set `REDIS_URL` |
| `worker` Dockerfile target (heavy ML, `CMD ["arq", …]`) | ✅ [Dockerfile](../../backend/Dockerfile) — **never built/deployed** | build `--target worker`, push, deploy |
| ARQ registration + KEDA pending-list marker | ✅ [worker.py](../../backend/app/jobs/worker.py) (`ww:gpu:pending` LPUSH/LREM) | wire the KEDA scale rule |
| Redis broker | ❌ `REDIS_URL` unset on dev → in-process fallback | Azure Cache for Redis (Basic C0) |
| GPU compute | ❌ no worker app exists | ACA GPU workload profile, scale-to-zero |
| Vector store (DINOv3 vectors) | ❌ "NOT yet in cloud" — code targets Qdrant at a Docker address | **pgvector** in Supabase (chosen, §4); Qdrant = scale-up |
| CI/CD for the worker image | ❌ `deploy-backend.yml` builds **only** `--target api` | add a worker build/deploy step or `deploy-worker.yml` |

**Key consequence:** because the enqueue switch is already keyed on `REDIS_URL`, *setting `REDIS_URL`
on the API alone flips uploads from in-process to ARQ offload.* If no worker is listening yet, jobs
queue but never run — so **Redis + worker must land together** (see [Rollout](#rollout-order)).

## 1. Build + push the worker image

Mirror the api tag scheme in [deploy-backend.yml](../../.github/workflows/deploy-backend.yml)
(`dev` branch → `dev-latest` + `<sha>`; `main` → `latest` + `<sha>`), but to a **separate repository**
`ww-backend-worker` so the api and worker images version independently:

```bash
docker build --target worker \
  -t <ACR>/ww-backend-worker:<image_tag> \
  -t <ACR>/ww-backend-worker:<github.sha> \
  -f backend/Dockerfile backend/
docker push <ACR>/ww-backend-worker:<image_tag>
docker push <ACR>/ww-backend-worker:<github.sha>
```

`--target worker` bundles `requirements-ml.txt` (torch, transformers, hdbscan, umap, speciesnet) +
the OpenCV system libs; `CMD ["arq", "app.jobs.worker.WorkerSettings"]`. It serves **no HTTP** — never
deploy it as the API (this is exactly why `deploy-backend.yml` pins `--target api`).

## 2. Compute target & GPU

**Recommendation: Azure Container Apps Consumption GPU workload profile** (`Consumption-GPU-NC8as-T4`,
NVIDIA T4). It is the smallest delta from the existing setup — same ACA environment, same `az
containerapp` tooling, **supports scale-to-zero** (GPU billed per-second only while a job runs), and
reuses the KEDA marker already in code.

`EMBEDDING_DEVICE` / `BIOCLIP_DEVICE` must be **`cuda`** on the GPU worker; leave them `cpu` only for
the deferred-GPU fallback below.

| Option | Cold start | Cost shape | Verdict |
|--------|-----------|-----------|---------|
| **ACA Consumption GPU profile** (recommended) | ~1–3 min (image pull + model download; mitigated by model cache, §7) | per-second GPU, **$0 when idle** (scale-to-zero) | ✅ smallest change, scale-to-zero, native KEDA |
| Azure Container Instances + GPU | similar pull cost; no native queue autoscale | per-second, but you script start/stop | ⚠️ more glue, loses the ACA/KEDA seam |
| AKS GPU node pool | warm if node pinned; else node provisioning | node billed whenever pool > 0 | ⚠️ overkill; cheapest only at sustained high volume |
| **CPU worker (GPU deferred)** | fast, no GPU quota needed | cheap CPU | ✅ valid interim — same image with `EMBEDDING_DEVICE=cpu`/`BIOCLIP_DEVICE=cpu`; SpeciesNet/BioCLIP run (slowly), DINOv3 ViT-H is impractical on CPU |

**GPU quota must be requested in-region first** — Consumption-GPU profile types/regions vary; confirm
availability in `australiaeast`/`australiasoutheast` before committing.

```bash
az containerapp env workload-profile add \
  --name <ACA_ENV> --resource-group WW-Website \
  --workload-profile-name gpu-t4 --workload-profile-type Consumption-GPU-NC8as-T4
```

## 3. Redis (broker + cross-process status)

Provision **Azure Cache for Redis, Basic C0** (~$16/mo, single node, no SLA — fine for dev; bump to
**Standard C1** for staging/prod for the replica/SLA). Azure Redis requires TLS on port **6380**, so
use a `rediss://` DSN.

```bash
az redis create --name ww-redis-dev --resource-group WW-Website \
  --location australiaeast --sku Basic --vm-size c0
# REDIS_URL=rediss://:<primary-access-key>@ww-redis-dev.redis.cache.windows.net:6380
```

Set `REDIS_URL` on **both** the api app and the worker app (same value):

```bash
az containerapp update --name ww-backend-dev --resource-group WW-Website \
  --set-env-vars REDIS_URL=rediss://:<key>@ww-redis-dev.redis.cache.windows.net:6380
```

**No enqueue code change.** [dispatch.py](../../backend/app/jobs/dispatch.py) already does:
`REDIS_URL` set → `create_pool` + `enqueue_job` + LPUSH the KEDA marker; empty → in-process
`runner.py`. Redis-unreachable falls back to local so a job is never lost. Status is mirrored to the
Supabase `api_jobs` table, so the API's `/api/jobs/{id}` polling works regardless of which process ran
the job.

## 4. Vector store — pgvector vs Qdrant (decision)

> **Decided: `pgvector` in Supabase. Qdrant will NOT be adopted** (not even as a scale-up path) and is
> being removed from the code. The comparison below is retained as the rationale; treat any "Qdrant
> option / scale-up" wording as historical. Canonical: [deployment-guide → Vector Store](../resources/deployment-guide.md#vector-store--pgvector-supabase).

DINOv3 embeddings need a vector store. Today `QDRANT_URL` defaults to `http://qdrant:6333` (a
Docker-network address that doesn't resolve in Azure) and the store is **"NOT yet in cloud"**
(deployment-guide) — so this is a genuinely **open choice, not a migration**. The embedding
*provenance* already lives in Postgres (`embedding_runs`: `model_name`, `model_version`,
`embedding_dim` 1280, `qdrant_collection`); only the **vectors** need a home. Two real options — a
dedicated engine, or "just use the Postgres you already run":

| | **pgvector** (in the existing Supabase Postgres) | **Qdrant** (dedicated; managed or self-host) |
|---|---|---|
| New vendor / infra | **None** — extension on the DB you already run | New service + its own ops & backup |
| Vectors beside relational data | **Yes** — one consistency domain, joins to `observations` | Separate store, cross-store sync |
| Auth / RLS / backup | **Inherits Supabase** (RLS + PITR cover the table) | Bespoke — needs its own snapshot job (the reserved `SUPABASE_QDRANT_BACKUP_BUCKET` DR path **isn't built yet**) |
| ANN latency @ ~1M (768–1280d) | HNSW ≈ **11 ms** p50; p95 climbs past ~5M | Rust + SIMD ≈ **4 ms** p50; sub-10 ms p95 well past 50M |
| **Filtered** search | post-filter on the candidate set — degrades on selective filters (~25 ms) | filters **inside** the HNSW traversal, payload-indexed (~1–2 ms) — best-in-class |
| Comfortable ceiling | **~5–10M vectors**; OOM risk above on small instances | 50M+; built-in quantization + horizontal scaling |
| Memory | index wants RAM: 1M×1280d ≈ 5 GB raw, ~10–15 GB w/ HNSW (≈ half with `halfvec`) | scalar/binary quantization built in; DINO tolerates binary well |
| Escape hatch *before* leaving Postgres | **pgvectorscale** (StreamingDiskANN, disk-backed; beat Qdrant in some 50M benchmarks; Filtered-DiskANN labels close the filter gap) | — |

**Recommendation for Wildlife Watcher: start on pgvector.** The deciding factors are *this* workload,
not generic benchmarks:
- **Scale is small.** One DINOv3 vector per media (or per animal crop) on a camera-trap project is
  realistically hundreds of thousands → low millions over years — squarely inside pgvector's
  comfortable ≤ 5–10M zone. Qdrant's wins (sub-10 ms p95 at 50M, horizontal scale) answer a scale
  problem this project doesn't have.
- **The filter weakness barely bites here.** Embedding/cluster queries are scoped by `deployment_id` /
  `project_id` (+ the §9 `embedding_model` version filter) — usually a few thousand vectors in one
  deployment, not a 10%-selective scan over millions. pgvector's post-filter penalty shows up on large,
  high-selectivity filters; that's not this query shape.
- **It deletes infra, a vendor, *and* a gap.** No second service, no `QDRANT_*` env, and DR is free
  (a Postgres table under Supabase PITR) — versus Qdrant, whose snapshot/DR job
  (`SUPABASE_QDRANT_BACKUP_BUCKET`) this spec itself notes **doesn't exist yet**.
- **§9 still works.** "Embedding version as a read filter" becomes `WHERE embedding_model = $1` + a
  partial index — the same contamination guard, in SQL.

**Keep Qdrant on the table for when** scale crosses ~5–10M, *or* fast high-selectivity filtered search
becomes a hot path, *or* you want managed quantization at scale. The in-Postgres step *before* that
jump is **pgvectorscale** (StreamingDiskANN) — it lifts the ceiling without leaving the database. Adopt
a dedicated engine only once a **measured** limit forces it, not a hypothetical one.

**If you do pick Qdrant** (now or later): Qdrant Cloud free tier
(`QDRANT_URL=https://<cluster>.qdrant.io:6333` + `QDRANT_API_KEY` on the worker; `QDRANT_COLLECTION`
defaults to `media_embeddings`), or self-host `qdrant/qdrant` as a second Container App with a
persistent `/qdrant/storage` volume — and **build the snapshot DR job**
(`QdrantService.create_snapshot()` + `SUPABASE_QDRANT_BACKUP_BUCKET`), which the pgvector path wouldn't
need.

> **Code impact is small either way** — the store is already behind a `get_qdrant_service()` seam, so a
> pgvector path is a sibling implementation writing to an `embeddings` table with a `vector(1280)`
> column + HNSW index. `embedding_runs` is store-agnostic (keep `qdrant_collection` as the *logical*
> space id, or rename to `vector_space`). The env (§5), cost (§7), and rollout (below) assume
> **whichever store you pick** — drop the `QDRANT_*` rows if pgvector.

## 5. Worker env / flags

The worker shares the storage/Supabase vars with the api **and** adds the ML/flag set. A fresh
container only has what you explicitly set; cross-reference the
[full-pipeline config checklist](../resources/deployment-guide.md#full-pipeline-config-checklist-per-subsystem).

| Group | Vars |
|-------|------|
| **Core / shared with api** | `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` (`secretref:`), `GENERAL_ORG_ID`, `REDIS_URL` (**same as api**) |
| **Storage (shared)** | `AZURE_STORAGE_CONNECTION_STRING` (+ `AZURE_STORAGE_CONTAINER_NAME`), `SUPABASE_MEDIA_BUCKET`, and `GOOGLE_DRIVE_ENABLED`/`GOOGLE_SERVICE_ACCOUNT_JSON`/`GOOGLE_DRIVE_FOLDER_ID` if the worker writes originals |
| **Pipeline flags** | `FF_PIPELINE_ENABLED=true`, `FF_SPECIESNET_ENABLED=true` (+ `SPECIESNET_RUN_MODE` — `single_thread` is only required for **in-process** API runs; the dedicated worker process may use `multi_thread`, but keep `single_thread` until tested), `FF_BIOCLIP_ENABLED=true`, `FF_WILDLIFE_BRAIN_ENABLED=true`, `FF_MEDIA_REGISTRY_ENABLED=true` |
| **Compute device** | `EMBEDDING_DEVICE=cuda`, `BIOCLIP_DEVICE=cuda` (GPU); `cpu` for the deferred-GPU interim |
| **Vector store** | **pgvector (chosen, §4): none** — reuses the Supabase vars above. Only for the Qdrant scale-up path: `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION` (default `media_embeddings`) |
| **Models** | `HF_TOKEN` (**gated DINOv3 access** — embeddings fail without it), `HF_HOME=/models/hf` (model cache, §7) |

The api app does **not** need the ML flags to *enqueue* (it only needs `REDIS_URL` + the arq client);
those flags gate the heavy work, which now happens in the worker. Keep the AI flags consistent across
api and worker so endpoint gating and execution agree.

## 6. CI/CD

Add a **worker build/deploy step**. Cleanest as a parallel job in
[deploy-backend.yml](../../.github/workflows/deploy-backend.yml) (shares the ACR login + env scoping)
or a sibling `deploy-worker.yml` triggered on the same `dev`/`main` pushes (+ `workflow_dispatch`).
Mirror the api environment scoping and tag scheme:

```yaml
# build-and-deploy-worker (sketch)
- name: Build worker image
  run: |
    docker build --target worker \
      -t ${{ secrets.ACR_LOGIN_SERVER }}/ww-backend-worker:${{ steps.target.outputs.image_tag }} \
      -t ${{ secrets.ACR_LOGIN_SERVER }}/ww-backend-worker:${{ github.sha }} \
      -f backend/Dockerfile backend/
- name: Push worker image
  run: |
    docker push ${{ secrets.ACR_LOGIN_SERVER }}/ww-backend-worker:${{ steps.target.outputs.image_tag }}
    docker push ${{ secrets.ACR_LOGIN_SERVER }}/ww-backend-worker:${{ github.sha }}
- name: Deploy worker
  run: |
    az containerapp update \
      --name ${{ steps.target.outputs.worker_app_name }} \
      --resource-group WW-Website \
      --image ${{ secrets.ACR_LOGIN_SERVER }}/ww-backend-worker:${{ steps.target.outputs.image_tag }}
    # worker_app_name: ww-embedding-worker-dev (dev) | ww-embedding-worker (staging)
```

Notes:
- The worker has **no `/health` HTTP endpoint** — drop the api's `curl …/health` verify step; verify
  via logs / replica list (§8) instead. ARQ's own `health_check_interval=30` writes worker health to
  Redis.
- The worker image is multi-GB (torch + models); the build job is slower than the api build. Layer
  caching in the Dockerfile (OpenCV apt libs placed **after** the ML pip layer) is already tuned for
  this. Consider only building the worker when `backend/requirements-ml.txt`, `backend/app/jobs/**`, or
  `backend/app/services/**` change to avoid a heavy rebuild on every api-only push.
- Keep `--min-replicas 0` on the worker `update` (don't inherit the api's `--min-replicas 1`).

## 7. Scaling & cost

> **Sequencing:** the KEDA marker + scale-to-zero below is the **steady-state cost optimisation**, not
> the first-cut-over posture. §12 recommends `--min-replicas 1` (no marker, no drift) for initial rollout
> and treats this section as what you adopt *after* retries, DLQ, and telemetry are in place.

**Scale rule (the one real gotcha).** ARQ enqueues to a Redis **sorted set** (`arq:queue`), which
KEDA's stock `redis` scaler (LLEN on a list) cannot read. The code already mirrors a plain-list marker
`ww:gpu:pending` (LPUSH on enqueue in dispatch.py, LREM on completion in worker.py), so KEDA scales on
that list's length:

```bash
az containerapp update --name ww-embedding-worker-dev --resource-group WW-Website \
  --min-replicas 0 --max-replicas 2 \
  --scale-rule-name redis-queue --scale-rule-type redis \
  --scale-rule-metadata listName=ww:gpu:pending listLength=1 \
                        address=ww-redis-dev.redis.cache.windows.net:6380 enableTLS=true \
  --scale-rule-auth password=redis-password-secret
```

- **min 0 / max 2.** Worker scales 0→1 when work is pending, back to 0 when drained. The marker is
  best-effort (a missing push only affects autoscaling, never correctness). `WorkerSettings.max_tries
  = 1` keeps the marker exactly-once, so a job can't be re-deferred after its marker was removed.
- **Cold-start model cache (§7 of deployment-guide).** A cold start re-downloads DINOv3/SpeciesNet
  (~1–3 min). Mount an **Azure Files** share and set `HF_HOME=/models/hf` so models survive
  scale-to-zero cycles.
- **Interim alternatives** if KEDA-on-list is fiddly: KEDA `cron` scaler to pre-warm during upload
  windows, or pin `--min-replicas 1` during an active tagging campaign and back to 0 after. Longer
  term, a cloud-native swap to an Azure Storage Queue + KEDA `azure-queue` scaler removes the marker
  entirely (bigger change at the dispatch seam, no drift risk).

**Rough cost** (dev, scale-to-zero):

| Component | Resource | Cost |
|-----------|----------|------|
| API | Container App, CPU, `api` image, min 1 | ~$15–40/mo |
| Redis | Azure Cache for Redis Basic C0 | ~$16/mo |
| **GPU worker** | ACA Consumption GPU (T4), min 0 | **GPU only while processing (per-second) — dominant cost; ~$0 idle** |
| Vectors | **pgvector** in Supabase (recommended, §4) — or Qdrant Cloud free tier | $0 |

GPU is the dominant variable cost; scale-to-zero keeps it near zero between upload bursts.

## 8. Verification

1. Trigger an AI run — upload images, or `POST /api/brain/reprocess/deployment/{id}`. API logs should
   show **`job_enqueued_arq`** (not `job_enqueued_local`).
2. `az containerapp replica list --name ww-embedding-worker-dev -g WW-Website` shows the worker scaling
   **0 → 1**.
3. Worker logs show `arq_worker_startup` (with `functions=<n>`), then per-job completion lines
   (e.g. `auto_embed_complete` with a cluster count).
4. A test upload produces **SpeciesNet observations** (animal rows with bbox/crop) and, with the
   Wildlife Brain on, Annotations → **Group → Cluster (embeddings)** populates from the vector store.
5. Queue drains → worker scales back to **0**.
6. Confirm Redis connectivity: `az redis show … ` reachable and `REDIS_URL` identical on api + worker.

```bash
# Worker logs (no /health endpoint — use logs/replicas)
az containerapp logs show --name ww-embedding-worker-dev -g WW-Website --type console --follow
```

## 9. Model & embedding versioning (cluster consistency)

**Why it matters:** when SpeciesNet v1→v2, BioCLIP updates, or DINOv3 weights change, the **new
embeddings are not comparable to the old ones** — clustering/similarity silently degrades the moment
vectors from two model versions share a space. Labels are recoverable (just re-classify); a
**contaminated vector space is not** — you can't tell which points are stale. This is the one piece of
state where a model bump is *destructive*, so versioning is load-bearing, not nice-to-have.

**Already exists — don't rebuild:**
- Observation rows carry the classifier version in `source_model_version` (`speciesnet-v4.0.1a`,
  `bioclip-2`) + `classified_by`; reprocessing is keyed on it (`delete_superseded_ai_observations`).
- Embedding runs already record `model_name` + `model_version` and the lifecycle module
  ([embedding_lifecycle.py](../../backend/app/domain/embedding_lifecycle.py)) has reprocess + compare
  entry points.

**What to add:**
1. **Per-job model manifest** — stamp every pipeline job (in the `api_jobs` mirror payload) with the
   exact versions it ran, so a job is self-describing for audit/repro. Source these from the existing
   `*_VERSION` constants + `EMBEDDING_DEFAULT_MODEL` — single source of truth, no new config:
   ```json
   {
     "pipeline_version": "2026.06",
     "speciesnet_version": "speciesnet-v4.0.1a",
     "bioclip_version": "bioclip-2",
     "embedding_model": "dinov3-vith-2026-06"
   }
   ```
2. **Embedding version on every vector row/point** (`embedding_model` + `embedding_dim`), and —
   the part that actually prevents contamination — **clustering/similarity queries must filter on the
   active `embedding_model` at read time** (`WHERE embedding_model = $1` in pgvector; a payload filter in
   Qdrant). A weights change then becomes a new logical space (new collection / partition, or
   filtered reads), reprocessed in the background and cut over when complete; old vectors are never
   silently mixed in.
3. **`pipeline_version`** — one bumpable string (e.g. a date tag) that changes whenever any stage's
   model *or* the step order changes. It answers "which deployments need reprocessing?" with a single
   predicate instead of cross-referencing three independent version fields.

**Decision:** `embedding_model` must be a **filter on read** (a `WHERE` predicate in pgvector; a payload
filter in Qdrant), not just stored metadata — metadata alone documents the drift; the filter is what
stops a model bump from corrupting clusters.

## 10. Observability & monitoring

Infra without telemetry is unrunnable in prod. Stand up dashboards (Azure Monitor / Log Analytics on
the ACA environment — ARQ + structlog already emit structured events) across three layers:

| Layer | Metrics | Source |
|-------|---------|--------|
| **Queue** | pending depth (`LLEN ww:gpu:pending`), enqueue→start wait, jobs/sec, age of oldest pending | Redis + `job_enqueued_arq` events |
| **Worker** | cold starts (scale 0→1 count), replica count, GPU utilisation + VRAM, host memory, crash/restart count | ACA replica metrics + container stdout |
| **ML / IO** | avg SpeciesNet runtime, avg BioCLIP-per-crop runtime, avg DINOv3 embed runtime, vector-store write latency + error rate | per-step `*_complete` logs (already carry `duration_seconds`) |

**Cheap win:** every step already logs `duration_seconds` (`classify_step_complete`,
`classify_crops_complete`, embedding completions) — pipe those into Log Analytics and most ML metrics
come for free. Add two alerts that catch the common prod failures early: **queue-depth / oldest-pending**
(worker stuck or under-scaled) and **job-failure-rate**.

## 11. Batching strategy (the main GPU cost lever)

Per-image jobs waste GPU: each invocation re-pays model load on a cold worker, and a T4 classifying one
crop at a time sits mostly idle. **Batch at the worker** — especially BioCLIP and DINOv3, both
throughput-bound matrix ops where batching is a **2–5× efficiency gain**:

- **Window:** drain when the pending batch hits **N items** *or* **T seconds** elapsed (whichever
  first). Start ~`N=32` (matches `EMBEDDING_BATCH_SIZE`), `T≈30s`.
- **Low-effort, because the seam exists:** the classifier and embedder already take a **list of paths**
  (`classifier.classify([...])`, batched embedding extraction). The per-crop step already classifies all
  of *one frame's* crops in a single `classify()` call — batching just extends that list **across frames**
  in a drain. It's job-shaping (coalesce crops into one job), not a model-call change.
- **Bound busy frames:** cap crops-classified-per-frame (already noted in the per-crop spec) so one
  pathological frame can't dominate a batch.

Net: fewer cold starts, higher T4 occupancy, lower $/image — the single biggest cost lever in this spec.

## 12. Reliability: queue-drift, retries & dead-letter

Two coupled smells the current design carries — **resolved together**:

**(a) Dual-queue drift.** The KEDA marker (`ww:gpu:pending`) is a *second* queue beside ARQ's sorted
set. If `enqueue` succeeds but the marker `LPUSH` fails → the job runs late or never scales; if the
marker outlives the job → a needless wake. The code labels this "best-effort," which is honest, but it's
a real production-failure surface, not just a tidiness nit.

**(b) `max_tries = 1`.** Deliberately coupled to the marker (a retry would strand the exactly-once
marker — see [worker.py](../../backend/app/jobs/worker.py)), but the consequence is that **any** transient
failure — GPU OOM, HF download timeout, Redis blip, vector store unavailable, a single corrupt frame —
*permanently* fails the job with no retry. That's dangerous for an ML pipeline where transient faults are
routine.

**Recommended resolution — one move fixes both.** Make `--min-replicas 1` the **initial-rollout**
posture (the spec already floats it as an interim; promote it to the default): a warm worker removes
KEDA-on-marker entirely (no drift, no cold starts) **and** decouples `max_tries` from the marker, so you
can adopt a real policy:
```
max_tries   = 3
retry_delay = [30, 120, 300]   # seconds, exponential-ish backoff
```
- **Classify failures** so retries are spent wisely:
  - *Retryable* (retry w/ backoff): network / HF download timeout, Redis disconnect, vector store
    unavailable, transient GPU OOM.
  - *Permanent* (fail fast, **no** retry — raise a non-retryable error): corrupt / unsupported media,
    invalid model input. Don't burn 3 attempts on a file that will never decode.
- **Dead-letter + inspection:** on final failure, persist `error_class` + message + the model manifest
  (§9) to the `api_jobs` mirror and a `gpu:dead` list, so failed jobs are **inspectable and replayable**
  rather than silently gone. `/api/jobs/{id}` already surfaces status — extend it with the failure reason.
- **Scale-to-zero (§7) becomes a later cost optimisation**, earned *after* retries + DLQ + telemetry
  exist — or skipped entirely for an **Azure Storage Queue + KEDA `azure-queue` scaler** (single source of
  truth, no marker) as the cloud-native end state.

This reorders the priorities: **min-replicas=1 + retries + telemetry first; scale-to-zero is a cost
optimisation you earn once the pipeline is observable and self-healing**, not a day-one requirement.

## Rollout order

1. **Vector store** ready (§4): for the recommended **pgvector** path, just enable the extension +
   create the `embeddings` table — nothing external to provision. (If Qdrant: cluster up,
   `QDRANT_URL`/`QDRANT_API_KEY` ready.)
2. **Redis** provisioned; DSN ready. *(Do not set `REDIS_URL` on the api yet — that flips enqueue to
   ARQ, and with no worker listening, jobs would queue unprocessed.)*
3. **Build + push** the worker image (`--target worker`).
4. **GPU workload profile** added to the ACA environment (quota approved first).
5. **Create the worker app** with the full env set (§5) + model cache (§7). For **initial rollout use
   `--min-replicas 1`** (no KEDA marker, no cold starts, no drift — see §12) with `max_tries=3` + backoff
   (§12). Defer scale-to-zero + the KEDA scale rule (§7) until retries, DLQ, and telemetry (§10, §12) are
   in place.
6. **Now set `REDIS_URL` on the api** (and confirm it's identical on the worker). This is the switch
   that turns on offload.
7. **Stamp the model manifest** (§9) on jobs and add the `embedding_model` read filter *before*
   the first real embedding run, so the vector space is versioned from point zero (retrofitting version
   tags onto an already-populated store is the expensive path).
8. **Verify** (§8) on dev, with the §10 dashboards/alerts live. Promote to staging/prod by repeating with
   the staging-scoped resources (`ww-embedding-worker`, `ww-redis`, Standard-tier Redis). **Scale-to-zero
   is a post-rollout cost optimisation** (§12), not part of first cut-over.

**Dependency:** [per-crop-classification-spec.md](_archive/per-crop-classification-spec.md) (BioCLIP per crop)
is **blocked on this worker** — its `classify_crops` step is a GPU job. Ship this infra first, confirm
SpeciesNet + BioCLIP + DINOv3 run on the worker, then land the per-crop change behind
`FF_PER_CROP_CLASSIFY_ENABLED`.

## Notes / decisions

- **No application/deploy code changes in this spec.** The dispatch switch, ARQ registration, and KEDA
  marker are already implemented. This is provisioning + config + a CI step. Leaving the code untouched
  also means the in-process fallback keeps working anywhere `REDIS_URL` is empty (local `dev` image).
- **Why Redis + worker must land together:** setting `REDIS_URL` on the api is the *only* trigger
  needed to start offloading; a worker must already be consuming the queue or jobs pile up. Hence the
  ordered rollout (api `REDIS_URL` is step 6, after the worker exists).
- **Separate ACR repo (`ww-backend-worker`)** rather than a shared repo with target-specific tags: the
  worker image is multi-GB and versions on a different cadence (ML deps) than the lean api; keeping
  them separate avoids confusing `--target api` and `--target worker` artifacts under one tag scheme.
- **`SPECIESNET_RUN_MODE`:** `single_thread` is mandated only for **in-process** API execution (the
  torch.fx detector trace races across threads/forks). On the dedicated worker it *can* relax to
  `multi_thread` for throughput, but treat that as a follow-up tuning step — keep `single_thread`
  until measured.
- **Deferred-GPU interim is real:** the same worker image with `EMBEDDING_DEVICE=cpu` /
  `BIOCLIP_DEVICE=cpu` and a CPU profile runs SpeciesNet + BioCLIP (slowly) and unblocks the
  *plumbing* (Redis/ARQ/worker) without GPU quota; DINOv3 ViT-H clustering is impractical on CPU, so
  the Wildlife Brain stays effectively GPU-gated.

## Documentation updates required (when this lands) — ✅ done

- ✅ **[resources/deployment-guide.md](../resources/deployment-guide.md)** — the *Backend Deployment →
  Architecture* note and the *GPU Worker* section now describe the **live** worker rather than a target
  architecture, and point at `cloud-infrastructure.md` / `prod-worker-provisioning-runbook.md` instead
  of repeating provisioning commands.
- ✅ **deployment-guide → Vector Store** — recorded as **current**: `media_embeddings.embedding`, the
  `match_media_embeddings` RPC, the `embedding_model` read filter, no ANN index yet.
- ✅ **[onboarding/04-AI-PIPELINE.md](../onboarding/04-AI-PIPELINE.md)** — states that the pipeline runs
  in a dedicated ARQ GPU worker and that AI is *off* on any backend without `REDIS_URL` + a worker.
- ✅ **[onboarding/01-TECHNOLOGY-STACK.md](../onboarding/01-TECHNOLOGY-STACK.md)** — Redis added to the
  external-services list.

> ⚠️ **Two decisions in this spec were overridden by reality — do not copy them:** the broker is an
> internal **Container App** running `redis:7-alpine`, *not* Azure Cache for Redis; and the worker
> scales on a **KEDA PostgreSQL scaler** over `api_jobs`, *not* the `ww:gpu:pending` Redis list marker
> described in §7 (the ACA KEDA operator cannot reach the internal Redis). Live truth:
> [cloud-infrastructure.md](../resources/cloud-infrastructure.md).

- This spec → move to `_archive/` once the worker is live in staging.
