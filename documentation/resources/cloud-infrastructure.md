# Cloud Infrastructure — Inventory & Maintenance

> **Status:** 🟢 Living — the authoritative map of every cloud resource the platform uses, what it's
> for, and how to keep it from drifting into sprawl. Pairs with [deployment-guide](deployment-guide.md)
> (*how to deploy*); this doc is *what exists and how to maintain it*. **Re-audit quarterly** (see the
> [review checklist](#periodic-review-checklist)). Last updated: **2026-07-09**.

> ### ✅ Single-RG AU East (migration complete — 2026-06-30)
> The org runs **only the `WW-AE` resource group** (`australiaeast`). The old `WW-Website` RG and the three
> `DefaultResourceGroup-*` RGs (plus all `mlwebsite*` Azure ML orphans) were **deleted on 2026-06-30** —
> `az group list` should return **only `WW-AE`**. If another RG ever appears, it's sprawl: investigate.
> *(One unrelated soft-deleted vault, `secrets-staging` in `newzealandnorth`, was intentionally left alone —
> decide separately whether to recover or purge it.)*

The platform spans four providers. Keep this list complete — an unlisted resource is either undocumented
or orphaned, and both are bugs.

| Provider | What it hosts |
|----------|---------------|
| **Azure** | Backend API + ARQ worker (Container Apps), container registry, upload blob buffer, Redis broker, logs |
| **Supabase** | Postgres + RLS, Auth, Storage (renditions), the system of record — **two projects** (dev / prod) |
| **Cloudflare** | Frontend (Pages + per-branch previews), DNS for `wildlifewatcher.ai`, CDN |
| **Google Drive** | Permanent original-image archive (`gdrive://`) |

---

## Azure

**Subscription:** `Microsoft Azure Sponsorship` (`f14bd966-e052-4c53-b26c-459dc764c33c`).
✅ **GPU is live (2026-07-03).** The ML worker runs on a **serverless T4 GPU** — a
`Consumption-GPU-NC8as-T4` workload profile named **`gpu-t4`** on `ww-env`, with the worker on `cuda`
and **scale-to-zero** (idle cost ≈ $0). This works on the sponsorship sub: ACA *serverless* GPU did not
require the `NCasv3_T4` VM-family quota we'd assumed was blocking it. See the worker row + scaling note.
✅ **Wildlife Brain is live end-to-end on dev (2026-07-09).** The full DINOv3 → **pgvector** →
HDBSCAN clustering → similarity-search path runs on the GPU worker (gated DINOv3 weights load via the
`HF_TOKEN` secret). The former Qdrant container is **removed** — vectors now live in the Supabase
`media_embeddings.embedding` column.

**Convention:** **everything** lives in **one resource group, `WW-AE`**, in **Australia East**. There is
no other RG. Do not let tooling auto-create resource groups (`DefaultResourceGroup-*`), other regions, or
extra Log Analytics workspaces — that is the main source of sprawl and cost confusion.

### Resources (`WW-AE` RG, `australiaeast`)

| Resource | Type | Purpose | Status | Maintenance |
|----------|------|---------|--------|-------------|
| `ww-env` | Container Apps **environment** | Hosts all container apps; workload profiles **`Consumption`** (CPU apps) + **`gpu-t4`** (`Consumption-GPU-NC8as-T4`, serverless T4 for the worker); KEDA. | ✅ | Region-locked — recreating it means recreating its apps. GPU profile: `az containerapp env workload-profile list -g WW-AE -n ww-env`. |
| `wwregistry` | Container Registry (ACR) | Image repos `ww-backend` (api, `--target api`) + `ww-backend-worker` (worker). | ✅ | Standard SKU, admin-enabled; `az acr build` builds in-cloud (no local Docker). Login server `wwregistry.azurecr.io`. **`weekly-purge` task** keeps sha tags to the 5 newest per repo (SHA-only filter; `stable`/`latest`/`dev-latest` immune) — see the cost-review action. |
| `wwuploadsae` | Storage account | **Azure Blob** temporary upload buffer (deleted after Drive archival). | ✅ | Connection string is an ACA secret on the apps. |
| `ww-redis-dev` | Container App | **ARQ broker** (internal `redis:7-alpine`, TCP, `exposedPort=6379`). 0.25 vCPU / 0.5 Gi (right-sized 2026-08-03). | ✅ | Reach app-to-app via the **short name** `redis://ww-redis-dev:6379` (the `.internal.*` FQDN times out for TCP). **Not KEDA-reachable** — see the worker row. |
| `ww-backend-dev` | Container App | **Dev API** (`--target api`), **min-0** (scale-to-zero), external ingress. `REDIS_URL` → offloads jobs to the worker. FQDN `ww-backend-dev.bravesand-8bd2f1d4.australiaeast.azurecontainerapps.io`. | ✅ | Dev Supabase project; secrets as **ACA secrets**. **`min-replicas` is set by CI, not by hand** — see the note below. |
| `ww-embedding-worker-dev` | Container App | **Dev ML worker** (`--target worker`) on the **`gpu-t4` serverless T4 GPU** profile. SpeciesNet detect + per-crop BioCLIP + DINOv3 (Wildlife Brain, live). ~1–2 s/image vs ~30–50 s on CPU. | ✅ | `EMBEDDING_DEVICE=cuda` / `BIOCLIP_DEVICE=cuda` (SpeciesNet auto-detects CUDA). Gated DINOv3 weights need the **`HF_TOKEN`** ACA secret (else `image_count=0`). Scales **0↔1** via **KEDA Postgres scaler on `api_jobs`** (see below). Secrets as ACA secrets. |
| `ww-backend` | Container App | **Prod API** (`--target api`), serving the production website. FQDN `ww-backend.bravesand-8bd2f1d4.australiaeast.azurecontainerapps.io`. | ✅ | Prod Supabase project. Google Drive archival configured **2026-07-26**: `GOOGLE_DRIVE_ENABLED`, `GOOGLE_DRIVE_FOLDER_ID`, secret `google-sa-json` (⚠️ currently the **same service account as dev**, `ww-drive-uploader@…` — split before it matters for rotation/audit). Before that date all three were unset, so prod stored no images at all. Console logs can lag ~1 day — don't use them to prove live traffic. |
| `ww-kv-dev-ae` | Key Vault | **Shared dev `.env`** — one secret, `ww-website-dotenv`, holding the whole file; fetched by `scripts/fetch-env.sh`. | ✅ | Uses **legacy access policies, not RBAC** (`enableRbacAuthorization: false`), so portal/CLI access needs an explicit policy entry — an ungranted developer sees *"The operation 'List' is not enabled in this key vault's access policy"*. Grant by **object id** (most of the team are tenant **guests**, so `--upn <dev>@wildlife.ai` fails): `az ad user list --filter "mail eq '<dev>@wildlife.ai'" --query "[0].id" -o tsv` → `az keyvault set-policy -n ww-kv-dev-ae --object-id <id> --secret-permissions get list`. Maintainers push updates with `az keyvault secret set … --file .env`. |
| Log Analytics workspace | Log Analytics | Container Apps logs (auto-created with the env). | ✅ | Keep exactly one. |

> **`min-replicas` is owned by [`deploy-backend.yml`](../../.github/workflows/deploy-backend.yml), not by
> `az`.** The deploy step passes `--min-replicas` on every run — **dev 0, prod 1** — so a manual
> `az containerapp update --min-replicas …` survives only until the next deploy of that branch. Change the
> policy in the workflow's *Determine target* step, or it silently reverts. (Prod stays warm so the public
> "Try the demo" button doesn't hit the ~1-min cold start; dev doesn't need that.)
>
> ✅ **Live since 2026-08-03** (applied via `az` after the workflow change, so CI and reality agree).
> `az containerapp show -n ww-backend-dev -g WW-AE --query properties.template.scale` → `minReplicas: 0`.

**Worker scaling (the one subtlety).** The ARQ broker is an internal Redis container — fine for
**app-to-app** (worker/API reach it by short name) but **the ACA KEDA operator cannot reach it**
(`connection refused` to the pod). So the worker does **not** scale on Redis. Instead a **KEDA
PostgreSQL scaler** queries the dev Supabase `api_jobs` table (reachable over IPv4 via the shared pooler):

```
SELECT count(*) FROM api_jobs WHERE status IN ('queued','processing')
  AND updated_at > now() - interval '15 minutes'  -- targetQueryValue=1 (dev; see window caveat)
```

A pending job → `count ≥ 1` → KEDA scales the worker **0→1** (on the GPU node); queue drains → back to
**0**. The pooler connection string lives in the ACA secret **`pg-conn`** (must point at the **dev**
project's *shared Session pooler* — `postgres.<ref>@aws-…pooler.supabase.com:5432`, using the
**database password**, not the service-role key). Use **Session mode (:5432)**, not Transaction (:6543):
the KEDA Postgres scaler reuses prepared statements, which Transaction pooling rejects with `42P05`
(*prepared statement already exists*) — that broke the scaler until we moved to :5432. Both ports are on
the same shared pooler host (IPv4). **Verified: scales to 0 when idle** (GPU T4, so idle cost ≈ $0). If
`pg-conn` is wrong/unset the scaler errors and KEDA holds the worker at its current replica count
(functional, just not scaling down).

> ⏱ **Window caveat (`15 minutes` on dev).** The annotate job only refreshes `updated_at` at the *start
> of each pipeline step* (`_on_step`), not within a step, so a single long step (SpeciesNet over thousands
> of images) could exceed the window with no heartbeat and let KEDA scale the worker to **0 mid-inference,
> killing the job**. Dev's small test deployments stay well under 15 min. **Prod must keep a generous
> window (≥ the reaper's 60 min) or add an intra-step heartbeat first** — see
> [prod-worker-provisioning-runbook](prod-worker-provisioning-runbook.md).

> 🚨 **Stuck-GPU guard (added 2026-07-09).** An Azure Monitor alert **`ww-gpu-worker-stuck-dev`** fires
> when the worker sits at **≥ 1 replica > 30 min** (action group **`ww-gpu-alerts`** → email). This is the
> primary defence against an idle T4 burning money if the scaler or a job status ever wedges — it never
> risks killing live work, so it's the guard to add *first* in any new environment.

> ⚠️ **Gotcha — never set the scale rule via `az containerapp update --scale-rule-metadata`.** The CLI
> silently drops metadata values containing spaces/quotes (i.e. the SQL), leaving `query`/`targetQueryValue`
> **empty**. With `min=0` an empty query means KEDA never wakes the worker → **uploads queue but AI never
> runs.** Edit the rule in the **Portal** (Container App → *Scale and replicas* → `pg-pending` rule) or via a
> full `--yaml` manifest. Confirm with:
> `az containerapp show -n ww-embedding-worker-dev -g WW-AE --query "properties.template.scale.rules[0].custom.metadata"`.

**GPU verification / readiness.** Confirm the worker actually has the GPU with
`az containerapp exec -n ww-embedding-worker-dev -g WW-AE --command "python -c \"import torch;print(torch.cuda.is_available())\""`
(→ `True`). Pipeline code is GPU-ready: BioCLIP/DINOv3 read `BIOCLIP_DEVICE`/`EMBEDDING_DEVICE`
(`services/bioclip_service.py`, `services/dinov3.py`); SpeciesNet passes no device and auto-detects CUDA.
The `ww-backend-worker:dev-latest` image already ships CUDA-enabled torch.

### Cost review — 2026-07-28 (30 days, Jun 30 – Jul 29: **NZ$212.84**)

Two line items are 99.98% of the bill. Recorded here so the next review compares against real numbers
rather than re-deriving them.

| Service | 30 days | Note |
|---|---|---|
| Azure Container Apps | NZ$168.91 | A flat ~NZ$5.50–6/day floor + 4 GPU spikes to NZ$12–15 |
| Container Registry | NZ$43.91 | Measured 2026-08-03: **Standard SKU + ~50 GiB storage overage** (150.4 GiB used vs 100 GiB included) — see the ACR action below |
| Everything else | NZ$0.03 | Storage, Key Vault, Monitor, bandwidth — all effectively free |

The **flat floor is the cost, not the GPU**: three always-on containers (`ww-backend-dev`, `ww-backend`,
`ww-redis-dev`) at roughly NZ$1.80–2/day each. The T4 spikes total only ~NZ$25–30/month, which is the
worker doing real work — scale-to-zero is behaving.

**Actions** (state as of 2026-08-03):

- [x] **`ww-backend-dev` → scale-to-zero** (~NZ$55/mo). Applied live via `az` **and** enforced by
      `deploy-backend.yml`, so it survives deploys.
- [x] **`ww-redis-dev` right-sized** 0.5 vCPU / 1 Gi → **0.25 / 0.5 Gi** (~NZ$12/mo). Done while the
      worker was at 0 replicas (nothing queued); replica confirmed `Running` after the revision.
- [x] **ACR overage eliminated — count-based purge executed 2026-08-07.** Prior state: Standard SKU,
      **150.4 GiB against the 100 GiB included** (~50 GiB overage ≈ the extra ~NZ$10/mo; age-based
      purge reclaimed 0 bytes — the whole registry postdates the 2026-06-29 `WW-AE` migration).
      Ran `acr purge --filter '<repo>:^[0-9a-f]{40}$' --ago 0d --keep 5 --untagged` on both repos
      (SHA-only filter, so `latest`/`dev-latest`/`stable` can never match): **80 tags + 74 manifests
      deleted, usage now 45.7 GiB** — under the included 100 GiB, ACR back to the flat ~NZ$33/mo.
      Before purging, the deployed images were pinned as **`:stable`** (`az acr import … --image
      <repo>:stable`) — a purge-proof rollback anchor; **re-point `stable` when you bless a new build**
      (`az acr import --force`). CI pushes a sha tag per deploy, so storage regrows — kept flat by the
      **`weekly-purge` ACR task** (created 2026-08-07): the same command, scheduled `0 14 * * 6` UTC
      (Sun ~2 am NZ). Check it with `az acr task list-runs -r wwregistry -n weekly-purge -o table`;
      it must be updated in lockstep if the tag scheme ever changes. Basic (10 GiB) stays out of
      reach while 5 multi-GB worker images are kept.

Consider whether prod's `ww-backend` needs min-1 while production has no real traffic — another ~NZ$55/mo,
but it's a product call: it's what keeps the public demo button fast.

### How to keep Azure clean
- **Audit:** `az resource list -o table` — every resource should be in `WW-AE` and map to a row above.
  Anything in another RG/region is sprawl.
- **Cost:** check **Cost Management → Cost analysis** monthly. The **GPU worker** is now the main variable
  cost — a T4 billed per-second **while running**. Keep it near $0 by confirming KEDA returns it to
  **0 replicas when idle** (`az containerapp replica list -n ww-embedding-worker-dev -g WW-AE -o table`
  → empty). A worker stuck at ≥1 replica is an idle T4 burning money — investigate the scaler.
- **Don't** create resources outside `WW-AE` / AU East without adding them here.

---

## Supabase

**Two projects** (do not cross the wires — the dev backend, worker, and KEDA scaler must all point at the
*same* project):

| Project ref | Environment | Used by |
|-------------|-------------|---------|
| `qegeovogqxiouqbrxmnh` | **dev** | `ww-backend-dev`, `ww-embedding-worker-dev`, the KEDA `pg-conn` scaler |
| `nuhwmubvygxyddkycmpa` | **prod / staging** | `ww-backend` |

Each holds Postgres (+ RLS, the access model), Auth (GoTrue), and Storage (public `media-renditions`
bucket). Schema is owned by [`ww-backend`](https://github.com/wildlifeai/wildlife-watcher-backend)
(`db:change` workflow). **Dev DB resets every deploy; staging/prod persists.** The chosen cloud vector
store is **pgvector in these projects** (see deployment-guide → Vector Store).

**Connection strings:** for tools that connect directly to Postgres (e.g. the KEDA scaler), use the
**shared Session pooler** (`...pooler.supabase.com:5432`) — the shared pooler host accepts **IPv4 by
default, no Pro add-on needed**. Use **Session mode (:5432)**, not Transaction (:6543): the scaler reuses
prepared statements and Transaction pooling rejects them with `42P05`. The *direct* `db.<ref>.supabase.co`
host and the *dedicated* pooler are IPv6-only (KEDA on Azure can't reach them). Treat the DB password as a
secret (ACA secret / vault, never in chat).

---

## Cloudflare

- **Pages** project hosts the React frontend with **per-branch previews** (`*.ww-website.pages.dev`;
  `dev.ww-website.pages.dev` is the dev preview).
- **DNS** for `wildlifewatcher.ai`; CDN + anonymous web analytics.
- Maintenance: deploys are git-driven (Pages build on push); no servers to patch. **After the AU East
  migration, the frontend's backend API URL + the API's CORS allow-list must point at the new
  `ww-backend-dev` / `ww-backend` FQDNs.**

## Google Drive

Originals are archived by one service account —
**`ww-drive-uploader@ww-drive-upload-photos.iam.gserviceaccount.com`** (GCP project
`ww-drive-upload-photos`) — shared by every environment. Only the *folder* differs.

**Folder layout.** Each environment writes into its own subfolder of a shared `Data` folder, and
the app creates the project/deployment tree beneath it:

```
Data/          1jIWV3OjSEnBK4Z64syHd2ugoRuXdVrK5   ← shared parent; NOT an upload target
├── dev/       1-F6cQc5lpYOJNSPKRs7i79Gh539N5hUT   ← ww-backend-dev + local dev
├── Production/ 1apf13KX075Fv4K0A2nbaTqOmCwZ9vJzr  ← ww-backend
└── (ad-hoc human folders — AE_photos_charles, temp_Tommy_WW_footage)
```

`GOOGLE_DRIVE_FOLDER_ID` has **no default in code** on purpose. It used to default to the `Data`
parent, so any environment that forgot to set it wrote into the shared root alongside real data.
Unset + Drive enabled now raises at upload time instead.

**Where the credential comes from — three different mechanisms, which is the usual source of
confusion:**

| Environment | Credential source |
|---|---|
| `ww-backend-dev`, `ww-backend` | ACA secret **`google-sa-json`** on each container app, injected as a `secretref`. Neither touches Key Vault. |
| Local dev | The `.env` in Key Vault (`ww-kv-dev-ae` → `ww-website-dotenv`), which carries the **inline JSON**. `bash scripts/fetch-env.sh` is all a developer needs. |
| Docker (dev compose) | `docker-compose.dev.yml` mounts a `service-account.json` at `/app/service-account.json`; the env var is a path in that case. |

`GOOGLE_SERVICE_ACCOUNT_JSON` accepts **either** inline JSON (starts with `{`) or a filesystem path
— see `services/google_drive.py::_creds`. Until 2026-08-27 the vault copy held a *Windows path* that
only resolved on one maintainer's machine, so `fetch-env.sh` could never produce working Drive
uploads; it now holds the JSON itself.

> Rotating the account means updating **three** places: the `google-sa-json` ACA secret on both
> container apps, and the `GOOGLE_SERVICE_ACCOUNT_JSON` line inside the vault `.env`.

---

## Periodic review checklist

Run quarterly (or before a cost review):

- [ ] `az resource list -o table` — **every** resource is in `WW-AE`; no other RGs/regions exist.
- [ ] No new `DefaultResourceGroup-*` appeared.
- [ ] Exactly one Log Analytics workspace.
- [ ] Worker (`ww-embedding-worker-dev`) returns to **0 replicas when idle** (KEDA `pg-conn` working) —
      else it's silently billing always-on.
- [ ] Container Apps `min/max-replicas` match intent (API min-1; worker min-0 + KEDA).
- [ ] ACR repo tags aren't accumulating unbounded (`az acr repository show-tags`).
- [ ] Secrets are ACA secrets, not plaintext env (the legacy dev app stored Supabase/Azure/Google
      secrets as plaintext — the rebuilt apps use ACA secrets; rotate any exposed keys).
- [ ] Cost Management has no surprise line items.
- [ ] `python scripts/parity_audit.py` reports **no unexplained gaps** (see below).

## Dev → prod parity audit

Three silent parity gaps reached production in two days (26 Jul 2026): a stale `media` RLS
predicate, missing `GOOGLE_DRIVE_*` config, and missing `FF_MEDIA_REGISTRY_ENABLED` +
`media-renditions` bucket. Each one made a feature fail invisibly — green ticks over empty tables.

**[`scripts/parity_audit.py`](../../scripts/parity_audit.py)** diffs the surfaces those gaps live
on: container-app **env vars** (names; values for `FF_*`/`*_ENABLED`), **secret names** (never
values), **storage buckets**, **auth providers**, and — via a printed SQL query whose JSON outputs
you feed back with `--sql-dev`/`--sql-prod` — **RLS policies, grants, function bodies (md5), and
the realtime publication**. Requires a logged-in `az` CLI; exit code 1 on unexplained gaps.

Rules of use:

- **Run it before testing any feature on production**, after prod config changes, and as part of
  this checklist.
- Intentional differences go in the `EXPECTED_*` allowlists **in the script, with the reason**
  (e.g. `REDIS_URL` — prod runs jobs in-process). An allowlist entry is a documented decision,
  not a mute button.
- A gap means one of two actions: apply the config to prod, or add it to the allowlist with a
  reason. Leaving it in the report is the only wrong option.

## Naming conventions

- Container apps: `ww-<role>[-dev]` (`ww-backend`, `ww-backend-dev`, `ww-embedding-worker-dev`, `ww-redis-dev`).
- ACR: `wwregistry` (`wwregistry.azurecr.io`); repos `ww-backend` (api) + `ww-backend-worker` (worker); tags `dev-latest` / `latest` / `<sha>` / `stable` (pinned rollback anchor — never purged; re-point on each blessed build).
- Storage: `wwuploadsae`. One resource group (`WW-AE`), one region (**Australia East**) for everything.
