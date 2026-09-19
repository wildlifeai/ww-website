# Documentation Index

Every doc in this repo, with its purpose and currency. **Living** docs track the code; **specs** are
active engineering hand-offs; **archive** is frozen history. Start with onboarding `00`.

## Onboarding — living, read in order

| Doc | Covers |
|-----|--------|
| [00-GETTING-STARTED](onboarding/00-GETTING-STARTED.md) | Local setup, env reference, run both services, verification checklist |
| [01-TECHNOLOGY-STACK](onboarding/01-TECHNOLOGY-STACK.md) | Every dependency + external service (Supabase, Cloudflare, Azure, Drive, vector store, iNat) + feature flags |
| [02-CODEBASE-GUIDE](onboarding/02-CODEBASE-GUIDE.md) | Repo layout, frontend nav/routes, backend router→domain→service layering |
| [03-DATA-AND-SYNC](onboarding/03-DATA-AND-SYNC.md) | Supabase, the RLS + GRANT model, async job system, the image-upload pipeline |
| [04-AI-PIPELINE](onboarding/04-AI-PIPELINE.md) | SpeciesNet detect→crop→classify, dedup + taxonomic roll-up, blank handling, DINOv3 Wildlife Brain |
| [05-ANNOTATION-WORKFLOW](onboarding/05-ANNOTATION-WORKFLOW.md) | Annotations grid, ribbon, full-screen labeling modal, review provenance |

## Reference guides — living

| Doc | Covers |
|-----|--------|
| [AI-ARCHITECTURE](resources/AI-ARCHITECTURE.md) | The three AI layers (Camera AI / Cloud AI / Wildlife Brain): canonical naming, cross-repo contracts, integration map |
| [api-reference](resources/api-reference.md) | Backend `/api/*` endpoint reference |
| [demo-account](resources/demo-account.md) | Read-only "Try the demo" account: access, 3-layer read-only enforcement, API usage, per-environment seeding |
| [deployment-guide](resources/deployment-guide.md) | Dev/prod environments, Azure + Cloudflare, CI/CD, vector store (pgvector in Supabase), security checklist |
| [cloud-infrastructure](resources/cloud-infrastructure.md) | **Inventory + maintenance** of every Azure/Supabase/Cloudflare/Drive resource: what exists, what's essential vs sprawl, quarterly review checklist |
| [prod-worker-provisioning-runbook](resources/prod-worker-provisioning-runbook.md) | Runbook to stand up the **production** GPU ML worker (ARQ + Redis + serverless T4) — ready to run once prod has real traffic |
| [ai-model-pipeline](resources/ai-model-pipeline.md) | Edge Impulse → Vela on-camera model conversion |
| [embedded-model-lifecycle](resources/embedded-model-lifecycle.md) | End-to-end on-device model flow across website / backend / mobile / firmware |
| [dual-ai-production-rollout](resources/dual-ai-production-rollout.md) | Runbook to promote dual-AI (Camera AI + Cloud AI / edge reflection) from dev → staging → production, incl. the firmware gating dependency and rollout checklist |
| [camtrapdp-import](resources/camtrapdp-import.md) | Importing CamtrapDP packages |
| [lorawan-webhook-setup](resources/lorawan-webhook-setup.md) | ⚠️ **Legacy prototype** — TTN / Chirpstack config for the website's FastAPI webhook, whose parser predates the WW500's FPort-2 TLV format. The **canonical production ingest** is the `lorawan-ingest` edge function in `ww-backend` (`documentation/resources/LORAWAN_INGEST.md`) |
| [ui-components](resources/ui-components.md) | Shared frontend design-system primitives |
| [testing-with-seed-users](resources/testing-with-seed-users.md) | Role-based seed users + access-control validation matrix |
| [WildlifeWatcher_model_preparation.ipynb](resources/WildlifeWatcher_model_preparation.ipynb) | Notebook: preparing/training a model for the camera before Edge Impulse export. Not maintained with the code — treat as a worked example |

## Active engineering specs — `development reports/`

Current hand-offs; kept up to date until the work ships, then moved to `_archive/`. Several below are
**shipped but deliberately retained** (marked ✅) because they still document *why* something is
shaped the way it is, or because a cross-repo half of the work is still open — each says so in its own
status banner. When in doubt, the living docs in `resources/` and `onboarding/` win over any spec here.

> How this folder works — writing a report, filing open items as issues, and the closing checklist —
> is in [`development reports/README.md`](development%20reports/README.md), which is also the
> status board for the table below.

| Spec | For | Covers |
|------|-----|--------|
| [2026-09_false-negatives-and-vlm-audit](development%20reports/2026-09_false-negatives-and-vlm-audit/README.md) | website + pipeline | 📋 Proposal / Analysis. Handling detector false negatives (SpeciesNet/MegaDetector), Motion ROI burst differencing, Wildlife Brain fallbacks, cascaded VLM auditing (PaliGemma/GPT-4o), and iNaturalist policy constraints |
| [dual-layer-ai-architecture-proposal](development%20reports/dual-layer-ai-architecture-proposal.md) | all repos | **Adopted; v0 merged.** Camera AI / Cloud AI / Wildlife Brain naming (canon now lives in [AI-ARCHITECTURE](resources/AI-ARCHITECTURE.md)), edge↔cloud integration framework, LoRaWAN alert-logic design (instant/digest/back-off), validation roadmap + user stories |
| [decoupled-upload-pipeline-spec](development%20reports/decoupled-upload-pipeline-spec.md) | ww-backend + website | 📋 Proposal, **not implemented**. Create `media` rows at ingest instead of inside the Drive job, + resumable backup sync, precheck/dedup and integrity audit — so a storage failure can't make photos vanish |
| [lorawan-alert-execution-spec](development%20reports/lorawan-alert-execution-spec.md) | website + backend + both firmwares | Build breakdown for `device_alert_rules` execution: manifest compile → Nordic strategy execution → Himax I2C score forward → backend uplink decode. Implements the proposal's §3 alert logic |
| [bmp-ingestion-analysis](development%20reports/bmp-ingestion-analysis.md) | website + firmware | ✅ Website side shipped. Raw-BMP ingest + in-pipeline JPEG re-compress; device capture behaviour; open firmware ask: #1 same-frame dual-write |
| [dual-camera-rpi-analysis](development%20reports/dual-camera-rpi-analysis.md) | firmware/hardware | HM0360 (night/IR) ↔ Raspberry Pi (day/colour) camera swap + dual-write interaction |
| [exif-telemetry-firmware-spec](development%20reports/exif-telemetry-firmware-spec.md) | firmware | Add temperature/battery to EXIF via UserComment (smallest change) |
| [ww-backend-schema-handoff](development%20reports/ww-backend-schema-handoff.md) | ww-backend | 📋 Open items for the schema/seed owners: the un-migrated iNat tables, the `qdrant_collection` rename, live GRANT/RLS parity, `dual_ai_v0` promotion, and the schema two website specs are waiting on |
| [access-test-seed-spec](development%20reports/access-test-seed-spec.md) | ww-backend | ✅ Shipped as `seeds/dev/access_test_deployments.sql`. Seed rows for the access-scenario upload fixtures (valid / no-access / not-found) |
| [storage-quota-spec](development%20reports/storage-quota-spec.md) | ww-backend + website | Per-org storage quota: `organisation_usage` schema + trigger + enforcement hook |
| [inaturalist-integration](development%20reports/inaturalist-integration.md) | website | ✅ Phases 1–4 implemented. iNaturalist publish + community-ID sync integration |
| [per-crop-classification-spec](development%20reports/per-crop-classification-spec.md) | website (pipeline) | Per-detection species classification (BioCLIP per crop) for mixed-species frames. **No longer blocked** — the GPU worker is live on dev; ships behind `FF_PER_CROP_CLASSIFY_ENABLED` |
| [gpu-worker-infra-spec](development%20reports/gpu-worker-infra-spec.md) | website (infra + CI/CD) | ✅ **Shipped (dev)** — kept as design history; ⚠️ §3 and §7 were overridden during the build. ARQ GPU worker: Redis + ACA GPU + pgvector + KEDA; versioning, observability, batching, retries. **Operate from** [cloud-infrastructure](resources/cloud-infrastructure.md) + [prod-worker-provisioning-runbook](resources/prod-worker-provisioning-runbook.md) |

## Archive — `development reports/_archive/` (frozen history)

Point-in-time plans, roadmaps, research spikes and audits. They capture *why* decisions were made and
are **not** kept current with the code.

| Doc | What it was |
|-----|-------------|
| [v2-architecture-plan](development%20reports/_archive/v2-architecture-plan.md) | The v2 architecture & execution plan (superseded by v4) |
| [v4-implementation-plan](development%20reports/_archive/v4-implementation-plan.md) · [v4-ui-roadmap](development%20reports/_archive/v4-ui-roadmap.md) · [v4-cost-model](development%20reports/_archive/v4-cost-model.md) | v4 planning set |
| [ui-redesign-roadmap](development%20reports/_archive/ui-redesign-roadmap.md) · [ui-navigation-roadmap](development%20reports/_archive/ui-navigation-roadmap.md) · [ui-personas-redesign-report](development%20reports/_archive/ui-personas-redesign-report.md) | UI redesign history (the 3-tab nav, status badges, Vega migration) |
| [viz-package-research-spike](development%20reports/_archive/viz-package-research-spike.md) | Charting library decision (resolved → Vega-Lite) |
| [annotation-pipeline-review](development%20reports/_archive/annotation-pipeline-review.md) | Point-in-time review of the annotation pipeline |
| [be-3-chart-specs-persistence](development%20reports/_archive/be-3-chart-specs-persistence.md) | Chart-spec persistence design note |
| [site-map](development%20reports/_archive/site-map.md) | Old site map (pre-Toolkit/Insights nav) |
| [gecko_monitoring_storyboard](development%20reports/_archive/gecko_monitoring_storyboard.md) | UX storyboard |

## Conventions

- **Living docs** (onboarding + resources) describe **current behaviour** and must be updated with the
  code. Nav/page names come from `App.tsx` `USER_TABS` — renaming a tab should trigger a doc grep for
  the old name.
- **Specs** (development reports, top level) are dated active hand-offs. When the work ships, drop a
  `🕰️ Historical snapshot` status banner and move the file into `_archive/`.
- Every development report carries a one-line **`> **Status:**`** banner under its title so currency is
  visible at a glance.
- The schema, RLS, and table GRANTs are owned by [`ww-backend`](https://github.com/wildlifeai/wildlife-watcher-backend) — never documented here as editable.
