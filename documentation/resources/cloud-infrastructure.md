# Cloud Infrastructure — Inventory & Maintenance

> **Status:** 🟢 Living — the authoritative map of every cloud resource the platform uses, what it's
> for, and how to keep it from drifting into sprawl. Pairs with [deployment-guide](deployment-guide.md)
> (*how to deploy*); this doc is *what exists and how to maintain it*. **Re-audit quarterly** (see the
> [review checklist](#periodic-review-checklist)). Last updated: **2026-11-26**.

> **AWS and REANNZ:** what exists there is in [AWS](#aws) and [REANNZ](#reannz) below; no site points at either. The move's status: [2026-10_aws-reannz-migration](../development%20reports/2026-10_aws-reannz-migration/README.md).

> ### ✅ Single-RG AU East (migration complete — 2026-06-30)
> The org runs **only the `WW-AE` resource group** (`australiaeast`). The old `WW-Website` RG and the three
> `DefaultResourceGroup-*` RGs (plus all `mlwebsite*` Azure ML orphans) were **deleted on 2026-06-30** —
> `az group list` should return **only `WW-AE`**. If another RG ever appears, it's sprawl: investigate.
> *(One unrelated soft-deleted vault, `secrets-staging` in `newzealandnorth`, was intentionally left alone —
> decide separately whether to recover or purge it.)*

The platform spans four providers, and two more, AWS and the REANNZ cluster, are being set up. Keep this list complete: an unlisted resource is either undocumented
or orphaned, and both are bugs.

| Provider | What it hosts |
|----------|---------------|
| **Azure** | Backend API + ARQ worker (Container Apps), container registry, upload blob buffer, CamtrapDP export ZIPs, Redis broker, logs |
| **Supabase** | Postgres + RLS, Auth, Storage (renditions), the system of record — **two projects** (dev / prod) |
| **Cloudflare** | Frontend (Pages, builds of `main` and `dev` only), DNS for `wildlifewatcher.ai`, CDN |
| **Google Drive** | Permanent original-image archive (`gdrive://`) |
| **AWS** | Being set up, dev only: the `ww-dev` stacks, which no site points at yet ([AWS](#aws)) |
| **REANNZ** | Being set up, dev trial: GPU batches under Spyfish's project `wildlife03546` until Wildlife Watcher's own is approved ([REANNZ](#reannz)) |

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
| `ww-env` | Container Apps **environment** | Hosts all container apps; workload profiles **`Consumption`** (CPU apps) + **`gpu-t4`** (`Consumption-GPU-NC8as-T4`, serverless T4 for both workers); KEDA. | ✅ | Region-locked, so recreating it means recreating its apps. GPU profile: `az containerapp env workload-profile list -g WW-AE -n ww-env`. |
| `wwregistry` | Container Registry (ACR) | Image repos `ww-backend` (api, `--target api`) + `ww-backend-worker` (worker). | ✅ | Standard SKU, admin-enabled; `az acr build` builds in-cloud (no local Docker). Login server `wwregistry.azurecr.io`. **`weekly-purge` task** keeps sha tags to the 5 newest per repo (SHA-only filter; `stable`/`latest`/`dev-latest` immune) — see the cost-review action. |
| `wwuploadsae` | Storage account | **Azure Blob** temporary upload buffer (deleted after Drive archival), one container per environment. Also `camtrapdp-exports`, the private container for the CamtrapDP export ZIPs (#387), shared by dev and prod: job ids never collide and nothing lists it. | ✅ | Connection string is an ACA secret on the apps. Public blob access is off for the account, so export links are read-only SAS links. The exports' lifecycle rule is [below](#wwuploadsae-lifecycle-rule). |
| `ww-redis-dev` | Container App | **ARQ broker** (internal `redis:7-alpine`, TCP, `exposedPort=6379`). 0.25 vCPU / 0.5 Gi (right-sized 2026-08-03). | ✅ | Reach app-to-app via the **short name** `redis://ww-redis-dev:6379` (the `.internal.*` FQDN times out for TCP). **Not KEDA-reachable** — see the worker row. |
| `ww-backend-dev` | Container App | **Dev API** (`--target api`), **min-0** (scale-to-zero), external ingress. `REDIS_URL` → offloads jobs to the worker. FQDN `ww-backend-dev.bravesand-8bd2f1d4.australiaeast.azurecontainerapps.io`. | ✅ | Dev Supabase project; secrets as **ACA secrets**. **`min-replicas` is set by CI, not by hand** — see the note below. |
| `ww-embedding-worker-dev` | Container App | **Dev ML worker** (`--target worker`) on the **`gpu-t4` serverless T4 GPU** profile. SpeciesNet detect + per-crop BioCLIP + DINOv3 (Wildlife Brain, live). ~1–2 s/image vs ~30–50 s on CPU. | ✅ | `EMBEDDING_DEVICE=cuda` / `BIOCLIP_DEVICE=cuda` (SpeciesNet auto-detects CUDA). Gated DINOv3 weights need the **`HF_TOKEN`** ACA secret (else `image_count=0`). Scales **0↔1** via **KEDA Postgres scaler on `api_jobs`** (see below). Secrets as ACA secrets. |
| `ww-backend` | Container App | **Prod API** (`--target api`), serving the production website, min 1 / max 2. `REDIS_URL` → `ww-redis` (since 2026-07-26), so it offloads jobs to `ww-embedding-worker`. FQDN `ww-backend.bravesand-8bd2f1d4.australiaeast.azurecontainerapps.io`. | ✅ | Prod Supabase project. Google Drive archival configured **2026-07-26**: `GOOGLE_DRIVE_ENABLED`, `GOOGLE_DRIVE_FOLDER_ID`, secret `google-sa-json` (⚠️ currently the **same service account as dev**, `ww-drive-uploader@…`; split it before it matters for rotation or audit). Before that date all three were unset, so prod stored no images at all. Console logs can lag ~1 day, so don't use them to prove live traffic. |
| `ww-redis` | Container App | **Prod ARQ broker**, the same internal `redis:7-alpine` TCP app as `ww-redis-dev` (`exposedPort=6379`, no volume). 0.5 vCPU / 1 Gi, min 1 / max 1, created 2026-07-26. | ✅ | Reach it as `redis://ww-redis:6379` (short name). Still at the runbook's size; dev was right-sized to 0.25 / 0.5 Gi on 2026-08-03. |
| `ww-embedding-worker` | Container App | **Prod ML worker** (`--target worker`) on the **`gpu-t4`** profile, 4 vCPU / 16 Gi, created 2026-07-26 from the [runbook](prod-worker-provisioning-runbook.md). Same pipeline as the dev worker, against the prod Supabase project; `REDIS_URL` → `ww-redis`. | ✅ | Scales **0↔1** on its own KEDA rule over **prod** `api_jobs`, 1-hour window (see below). `deploy-backend.yml` rolls it with `revision copy` on each push to `main`. Secrets as ACA secrets, including its own `pg-conn` and `hf-token`. |
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

**Worker scaling (the one subtlety).** The ARQ broker is an internal Redis container, fine for
**app-to-app** (worker/API reach it by short name) but **the ACA KEDA operator cannot reach it**
(`connection refused` to the pod). So the workers do **not** scale on Redis. Instead each worker has a
**KEDA PostgreSQL scaler**, rule `pg-pending`, that queries the `api_jobs` table of its own Supabase
project (reachable over IPv4 via the shared pooler):

```
SELECT count(*) FROM api_jobs WHERE status IN ('queued','processing')
  AND updated_at > now() - interval '<window>'  -- targetQueryValue=1
```

| Worker | Project | Window | Replicas | Stuck-GPU alert |
|---|---|---|---|---|
| `ww-embedding-worker-dev` | dev | `15 minutes` | 0 to 1 | `ww-gpu-worker-stuck-dev` (2026-07-09) |
| `ww-embedding-worker` | prod | `1 hour` | 0 to 1 | `ww-gpu-worker-stuck` (2026-07-26) |

Both rules poll every 30 s with a 300 s cooldown (checked with `az` on 2026-10-26).

A pending job → `count ≥ 1` → KEDA scales the worker **0→1** (on the GPU node); queue drains → back to
**0**. The pooler connection string lives in each worker's ACA secret **`pg-conn`**, which must point at
**that worker's own** project's *shared Session pooler* (`postgres.<ref>@aws-…pooler.supabase.com:5432`,
using the **database password**, not the service-role key). Use **Session mode (:5432)**, not
Transaction (:6543): the KEDA Postgres scaler reuses prepared statements, which Transaction pooling
rejects with `42P05` (*prepared statement already exists*), and that broke the dev scaler until we moved
to :5432. Both ports are on the same shared pooler host (IPv4). **Verified: scales to 0 when idle** (GPU
T4, so idle cost ≈ $0). If `pg-conn` is wrong or unset the scaler errors and KEDA holds the worker at its
current replica count (functional, just not scaling down).

> ⏱ **Window and heartbeat.** Since 2026-07-10 the annotate job runs inside `job_heartbeat`
> ([`jobs/store.py`](../../backend/app/jobs/store.py)), which refreshes `api_jobs.updated_at` every
> 60 s. Before that a single long step (SpeciesNet over thousands of images) wrote nothing until it
> ended, so a step longer than the window let KEDA scale the worker to **0 mid-inference, killing the
> job**. The heartbeat needs the heavy step to run in `asyncio.to_thread`, as SpeciesNet and BioCLIP do;
> a step that blocks the event loop silences it. The window is also how long a job row left at
> `processing` after its worker stops keeps the T4 up. Prod keeps the
> [runbook's](prod-worker-provisioning-runbook.md) 1 hour, the same as the 60-min stale-job reaper.

> 🚨 **Stuck-GPU guard.** One Azure Monitor metric alert per worker (named in the table above) fires
> when `Replicas` averages **≥ 1 over 30 min**, checked every 5 min, severity 2, to action group
> **`ww-gpu-alerts`** (email). This is the primary defence against an idle T4 burning money if the
> scaler or a job status ever wedges. It never risks killing live work, so it's the guard to add
> *first* in any new environment.

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

### `wwuploadsae` lifecycle rule

The export job writes each ZIP once and hands out a read-only link valid for 24 hours. A lifecycle
rule deletes the ZIP 7 days after it is written. Azure runs the rule about once a day, so a ZIP can
outlive 7 days by up to a day. Nothing in the code sweeps old exports.

```json
{
  "rules": [
    {
      "enabled": true,
      "name": "deleteCamtrapdpExports",
      "type": "Lifecycle",
      "definition": {
        "filters": { "blobTypes": ["blockBlob"], "prefixMatch": ["camtrapdp-exports/"] },
        "actions": { "baseBlob": { "delete": { "daysAfterModificationGreaterThan": 7 } } }
      }
    }
  ]
}
```

`management-policy create` replaces the account's whole policy. The account had none on
2026-10-26; if `az storage account management-policy show --account-name wwuploadsae -g WW-AE`
returns one, add this rule to its `rules` instead. With the JSON above saved as `lifecycle.json`:

```bash
az storage account management-policy create --account-name wwuploadsae -g WW-AE --policy @lifecycle.json
```

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
- **Cost:** check **Cost Management → Cost analysis** monthly. The **GPU workers** are now the main
  variable cost, a T4 each, billed per second **while running**. Keep them near $0 by confirming KEDA
  returns both to **0 replicas when idle** (`az containerapp replica list -n <worker> -g WW-AE -o table`
  → empty). A worker stuck at ≥1 replica is an idle T4 burning money: investigate the scaler.
- **Don't** create resources outside `WW-AE` / AU East without adding them here.

---

## AWS

> **Dev deployed, nothing live (2026-11-26).** The accounts, sign-in and the `ww-dev` stacks exist
> for the move in
> [2026-10_aws-reannz-migration](../development%20reports/2026-10_aws-reannz-migration/README.md).
> The dev API answers on AWS, but no site points at it, so no traffic reaches AWS.

| Piece | Setting | Why |
|---|---|---|
| Organization | wildlife.ai's own, all features on | The wildlife.ai account left an outside organization on 2026-11-19, so it pays its own bills and receives its own credits |
| Management account (`Wildlife.ai`) | Billing, the organization and IAM Identity Center, no workloads | Organization policies cannot restrict the management account |
| `ww-dev` | Member account for the dev stack | A dev mistake cannot reach prod data, and closing the account removes everything in it |
| In `ww-dev` (2026-11-26) | Stack `ww-shared`: ECR repository `ww-backend`, deploy role `ww-gha-deploy`. Stack `ww-dev`: functions `ww-dev-api` and `ww-dev-cpu`, an HTTP API (its address is the stack's `ApiUrl` output), bucket `wildlifewatcher-dev-media`, queue `ww-dev-dlq`, the 15-minute tick, alarms to the maintainer. SSM under `/ww/dev/`: the three `SUPABASE_*` values, the `FF_*` flags on as on Azure dev, `WORKER_TOKEN_SHA256`, `GPU_IMAGE`, `DEMO_EMAIL`, `DEMO_PASSWORD`, `GENERAL_ORG_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`. Lambda concurrency quota 1,000 | Deployed from a workstation; `infra/aws/` holds both templates |
| `ww-prod` | Not created yet; made like `ww-dev` at the prod step | |
| Region | Asia Pacific (New Zealand), `ap-southeast-6`, in every account | An opt-in region: a new account starts with it off ([Adding an account](#adding-an-aws-account)). It has no Lambda Function URLs and no CloudFormation budget resource (checked 2026-11-20), so the API sits behind an API Gateway HTTP API and the budget is set by hand |
| IAM Identity Center | Single-Region instance in Auckland, its own user directory | Multi-Region would copy users to US West (Oregon) and needs a KMS key |
| Permission set | `AdministratorAccess`, 8-hour sessions, assigned on `ww-dev` | |
| Deploy role `ww-gha-deploy` | In `ww-shared`, deployed with `Env=<env>`. Any job of this repository that runs in the account's GitHub environment (`dev`, or `staging` for `ww-prod`) can assume it; only `deploy-aws.yml` does, and neither environment has branch or reviewer rules (2026-11-26). It pushes to `ww-backend`, writes `/ww/<env>/GPU_IMAGE`, and runs `sam deploy` on stack `ww-<env>`: functions, roles, log groups, queue, topic and alarms named `ww-<env>-*`, the settings of bucket `wildlifewatcher-<env>-media` but not its objects, the region's HTTP APIs and default-group schedules. It cannot change itself or `ww-shared`, delete the stack or the bucket, or touch any other SSM parameter | Least privilege, reviewed on 2026-11-26 against CloudTrail of the workstation deploys ([#468](https://github.com/wildlifeai/ww-website/issues/468)). `sam deploy` passes no service role, so CloudFormation acts with this role's rights: a new resource type in `template.yaml` needs its actions in `shared.yaml` first, and a change to `ww-shared` is deployed from a workstation |
| Buckets | `wildlifewatcher-dev-media`, `wildlifewatcher-prod-media` | Each environment's stack creates its own; both names were free on 2026-11-19 |

Account IDs and the access portal address are not written in this repository, which is public.
They are in the access portal and the Identity Center dashboard, and GitHub keeps the ones CI
needs as environment variables.

### Signing in to AWS

People sign in to the member accounts through IAM Identity Center, never with access keys.

1. An administrator creates your Identity Center user and assigns a permission set on each
   account you need. A user created through the API or the CLI gets no welcome email, so the
   administrator then uses **Reset password** on the user to email you a link.
2. Set your password, sign in to the access portal and register a multi-factor device.
3. Install AWS CLI 2.32 or later (on Windows, `winget install --id Amazon.AWSCLI -e`).
4. Add the session and one profile per account to `~/.aws/config`, with the portal address and
   the account ID from the access portal:

   ```ini
   [sso-session wildlife]
   sso_start_url = <AWS access portal URL>
   sso_region = ap-southeast-6
   sso_registration_scopes = sso:account:access

   [profile ww-dev]
   sso_session = wildlife
   sso_account_id = <ww-dev account ID>
   sso_role_name = AdministratorAccess
   region = ap-southeast-6
   ```

5. Sign in once per 8 hours, and name the profile on each command:

   ```bash
   aws sso login --sso-session wildlife
   aws sts get-caller-identity --profile ww-dev
   ```

In Windows PowerShell a quoted path to the CLI needs the call operator,
`& 'C:\Program Files\Amazon\AWSCLIV2\aws.exe' sso login --sso-session wildlife`; without `&` it
is a parse error. A shell started after the install finds `aws` on its PATH.

Root of the management account keeps its multi-factor device. Until a permission set is
assigned on the management account, its organization and billing tasks are done as root.
Member accounts have no root password; leave it that way.

GitHub Actions uses neither: `deploy-aws.yml` assumes the `ww-gha-deploy` role from
`infra/aws/shared.yaml` through GitHub's OIDC provider, one role per account
([what it can change](#aws)).

### Adding an AWS account

From the management account:

```bash
aws organizations create-account --email <an address no other AWS account uses> --account-name ww-prod
aws organizations describe-create-account-status --create-account-request-id <id from the line above>
aws account enable-region --account-id <new account ID> --region-name ap-southeast-6
```

Then request 1,000 concurrent Lambda executions for the new account in Service Quotas (quota
`L-B99A9384`): a new account can start at 10, the environment stack reserves 15, and AWS always
keeps 100 unreserved, so the stack cannot deploy until the increase lands; `ww-dev` started
at 10. The organization's budget already covers a new account; raise it if the new account
adds steady spend. Last, assign the permission set on the new account in Identity Center and
add a profile for it like `ww-dev`'s. `enable-region` for another account works because the
organization's trusted access for AWS Account Management is on (since 2026-11-19).

### How to keep AWS clean
- **Audit:** every resource in a member account belongs to a CloudFormation stack, `ww-shared` or
  `ww-<env>`; anything else is sprawl. The management account holds no workloads.
- **Cost:** one AWS Budget for the whole organization, `wildlife-ai-monthly` (all services,
  US$20 a month, an email at 80% of actual spend), set in the management account's console on
  2026-11-20. It is not in a stack: CloudFormation in `ap-southeast-6` has no budget resource,
  and a member account cannot create one. A per-account budget filters by Linked account, which
  the console lists only after a day of that account's cost data. Check Billing and Cost
  Management monthly.
- **Access:** every Identity Center user is someone who still needs access.

---

## REANNZ

> **Dev trial, nothing live (2026-11-21).** The GPU half of the move in
> [2026-10_aws-reannz-migration](../development%20reports/2026-10_aws-reannz-migration/README.md):
> batches on the cluster that lease work from the AWS dev API. How to set it up is in that report.

| Piece | Setting | Why |
|---|---|---|
| Project | `wildlife03546`, Spyfish's, for the dev trial only | Wildlife Watcher's own project was applied for on 2026-11-21; the scripts switch to its code once approved |
| Access | Victor's login, `victorav`, through OnDemand at https://ondemand.nesi.org.nz (Apps, then Mahuika Shell Access; Files to upload). SSH would be `login.hpc.nesi.org.nz` through the jump host `lander.hpc.nesi.org.nz` | REANNZ's rules for AI agents (from 2026-11-01): an agent never logs in on its own, so Victor runs every command |
| Layout | `/nesi/project/wildlife03546/ww/`: `bin` (the scripts in `cluster/reannz/`), `sif`, `models`, `slurm_logs`, `logs`. Job scratch and the Apptainer cache under `/nesi/nobackup/wildlife03546/ww/` | nobackup deletes files untouched for 90 days |
| Image | `ghcr.io/wildlifeai/ww-backend-gpu:<tag>`, public. The organization allows public packages since 2026-11-21, and `ww-website` has Write on the package | The cluster pulls with no credential; `deploy-aws.yml` pushes new tags |
| Credentials | Only the lease token, `~/.config/ww/<env>.token`, mode 600; the API keeps its SHA-256 | No database or AWS key reaches the cluster |

On 2026-11-21 the cluster holds the SIFs for `dev-70edf58` (3.6 GB) and `dev-6bcff97`, the cached weights
(`hf` 7.3 GB, `kaggle` 0.5 GB), and the dev token and API address. No crontab is set.

### How to keep REANNZ clean
- `ww_pull.sl` keeps the two newest SIFs and deletes older ones.
- Weights are cached once per model version with `ww_cache_models.sh`; re-run it after a model
  change and delete the old version's folder by hand.

---

## Supabase

**Two projects** (do not cross the wires: each environment's API, worker and KEDA scaler must all point
at the *same* project):

| Project ref | Environment | Used by |
|-------------|-------------|---------|
| `qegeovogqxiouqbrxmnh` | **dev** | `ww-backend-dev`, `ww-embedding-worker-dev`, its KEDA `pg-conn` scaler |
| `nuhwmubvygxyddkycmpa` | **prod / staging** | `ww-backend`, `ww-embedding-worker`, its KEDA `pg-conn` scaler |

Each holds Postgres (+ RLS, the access model), Auth (GoTrue), and Storage (public `media-renditions`
bucket). Schema is owned by [`ww-backend`](https://github.com/wildlifeai/ww-backend)
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

- **Pages** project hosts the React frontend: `main` is production and `dev` the only preview
  branch (`dev.ww-website.pages.dev`; each build also gets `<id>.ww-website.pages.dev`). Branch
  control and what a build reports to GitHub: the deployment guide's
  [Preview Deployments](deployment-guide.md#preview-deployments).
- **DNS** for `wildlifewatcher.ai`; CDN + anonymous web analytics.
- Maintenance: deploys are git-driven (Pages build on push); no servers to patch. The frontend's
  `VITE_API_BASE_URL` and the API's `ALLOWED_ORIGINS` must name each other: when an API moves, change both.

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
- [ ] Both workers (`ww-embedding-worker-dev`, `ww-embedding-worker`) return to **0 replicas when idle**
      (KEDA `pg-conn` working), or they are silently billing always-on.
- [ ] Container Apps `min/max-replicas` match intent (prod API min 1, dev API min 0, both set by
      `deploy-backend.yml`; workers min 0 + KEDA).
- [ ] ACR repo tags aren't accumulating unbounded (`az acr repository show-tags`).
- [ ] Secrets are ACA secrets, not plaintext env (the legacy dev app stored Supabase/Azure/Google
      secrets as plaintext — the rebuilt apps use ACA secrets; rotate any exposed keys).
- [ ] Cost Management has no surprise line items.
- [ ] `python scripts/parity_audit.py` reports **no unexplained gaps** (see below).
- [ ] AWS: member accounts hold only the `ww-shared` and `ww-<env>` stacks, the management account
      holds no workloads, and every Identity Center user still needs access.

## Dev → prod parity audit

Three silent parity gaps reached production in two days (26 Jul 2026): a stale `media` RLS
predicate, missing `GOOGLE_DRIVE_*` config, and missing `FF_MEDIA_REGISTRY_ENABLED` +
`media-renditions` bucket. Each one made a feature fail invisibly — green ticks over empty tables.

**[`scripts/parity_audit.py`](../../scripts/parity_audit.py)** diffs the surfaces those gaps live
on: container-app **env vars** (names; values for `FF_*`/`*_ENABLED`), **secret names** (never
values), **storage buckets**, **auth providers**, and — via a printed SQL query whose JSON outputs
you feed back with `--sql-dev`/`--sql-prod` — **RLS policies, grants, function bodies (md5), and
the realtime publication**. Requires a logged-in `az` CLI; exit code 1 on unexplained gaps. It
compares the two APIs only, so worker env vars (where the AI flags must also be set) are not diffed.

Rules of use:

- **Run it before testing any feature on production**, after prod config changes, and as part of
  this checklist.
- Intentional differences go in the `EXPECTED_*` allowlists **in the script, with the reason**
  (e.g. the `analysis-images` bucket, legacy and prod-only). An allowlist entry is a documented
  decision, not a mute button.
- A gap means one of two actions: apply the config to prod, or add it to the allowlist with a
  reason. Leaving it in the report is the only wrong option.

## Naming conventions

- Container apps: `ww-<role>[-dev]` (`ww-backend`, `ww-backend-dev`, `ww-embedding-worker`, `ww-embedding-worker-dev`, `ww-redis`, `ww-redis-dev`).
- ACR: `wwregistry` (`wwregistry.azurecr.io`); repos `ww-backend` (api) + `ww-backend-worker` (worker); tags `dev-latest` / `latest` / `<sha>` / `stable` (pinned rollback anchor — never purged; re-point on each blessed build).
- AWS: accounts `ww-<env>`; stacks `ww-shared` (one per account) and `ww-<env>`; functions `ww-<env>-api` and `ww-<env>-cpu`; bucket `wildlifewatcher-<env>-media`; SSM parameters under `/ww/<env>/`; deploy role `ww-gha-deploy`. `<env>` is `dev` or `prod`; the GitHub environment for `prod` is `staging`.
- Storage: `wwuploadsae`. One resource group (`WW-AE`), one region (**Australia East**) for everything.
