# Documentation Index

Every doc in this repo, with its purpose. **Living** docs track the code; **specs** are
engineering hand-offs, whose status is on the [status board](development%20reports/README.md#reports);
**archive** is frozen history. Start with onboarding `00`.

## Onboarding — living, read in order

| Doc | Covers |
|-----|--------|
| [00-GETTING-STARTED](onboarding/00-GETTING-STARTED.md) | Local setup, env reference, run both services, verification checklist |
| [01-TECHNOLOGY-STACK](onboarding/01-TECHNOLOGY-STACK.md) | Every dependency and external service (Supabase, Cloudflare, Azure, Drive, AWS, REANNZ, vector store, iNat), the requirements files, the feature flags |
| [02-CODEBASE-GUIDE](onboarding/02-CODEBASE-GUIDE.md) | Repo layout, frontend nav/routes, backend router→domain→service layering |
| [03-DATA-AND-SYNC](onboarding/03-DATA-AND-SYNC.md) | Supabase, the RLS + GRANT model, async job system, the image-upload pipeline |
| [04-AI-PIPELINE](onboarding/04-AI-PIPELINE.md) | SpeciesNet detect→crop→classify, dedup + taxonomic roll-up, blank handling, DINOv3 Wildlife Brain |
| [05-ANNOTATION-WORKFLOW](onboarding/05-ANNOTATION-WORKFLOW.md) | Media grid, ribbon, full-screen labeling modal, review provenance |

## Reference guides — living

| Doc | Covers |
|-----|--------|
| [AI-ARCHITECTURE](resources/AI-ARCHITECTURE.md) | The three AI layers (Camera AI / Cloud AI / Wildlife Brain): canonical naming, cross-repo contracts, integration map |
| [api-reference](resources/api-reference.md) | Backend `/api/*` endpoint reference |
| [public-api-guide](resources/public-api-guide.md) | For partners: API keys, scopes, paging, errors, rate limits and the CamtrapDP export of the `/api/v1` public API, with curl examples |
| [demo-account](resources/demo-account.md) | Read-only "Try the demo" account: access, 3-layer read-only enforcement, API usage, per-environment seeding |
| [deployment-guide](resources/deployment-guide.md) | Dev/prod environments, Azure + Cloudflare, the settings each subsystem needs (the AWS and REANNZ ones included), CI/CD, vector store (pgvector in Supabase), security checklist |
| [cloud-infrastructure](resources/cloud-infrastructure.md) | **Inventory + maintenance** of every Azure, AWS, REANNZ, Supabase, Cloudflare and Drive resource: what exists, what's essential vs sprawl, quarterly review checklist |
| [prod-worker-provisioning-runbook](resources/prod-worker-provisioning-runbook.md) | Runbook that stood up the **production** GPU ML worker (ARQ + Redis + serverless T4) on 2026-07-26, kept to rebuild it |
| [ai-model-pipeline](resources/ai-model-pipeline.md) | Edge Impulse → Vela on-camera model conversion |
| [embedded-model-lifecycle](resources/embedded-model-lifecycle.md) | End-to-end on-device model flow across website / backend / mobile / firmware |
| [dual-ai-production-rollout](resources/dual-ai-production-rollout.md) | Runbook to promote dual-AI (Camera AI + Cloud AI / edge reflection) from dev → staging → production, incl. the firmware gating dependency and rollout checklist |
| [camtrapdp-import](resources/camtrapdp-import.md) | Importing CamtrapDP packages |
| [LORAWAN_INGEST](https://github.com/wildlifeai/ww-backend/blob/dev/documentation/resources/LORAWAN_INGEST.md) (ww-backend) | LoRaWAN ingest: network-server setup for the `lorawan-ingest` edge function. The website receives no uplinks |
| [ui-components](resources/ui-components.md) | Shared frontend design-system primitives |
| [testing-with-seed-users](resources/testing-with-seed-users.md) | Role-based seed users + access-control validation matrix |
| [WildlifeWatcher_model_preparation.ipynb](resources/WildlifeWatcher_model_preparation.ipynb) | Notebook: preparing/training a model for the camera before Edge Impulse export. Not maintained with the code — treat as a worked example |

## Active engineering specs — `development reports/`

Engineering hand-offs, kept until the work ships, then moved to `_archive/`. A few shipped ones are
kept on purpose because they still document *why* something is shaped the way it is, or because a
cross-repo half of the work is still open; each says so in its own status banner. When in doubt, the
living docs in `resources/` and `onboarding/` win over any spec here.

> How this folder works (writing a report, filing open items as issues, the closing checklist) and
> the status of every report below: [`development reports/README.md`](development%20reports/README.md),
> the status board. This table holds only what each report covers.

| Spec | For | Covers |
|------|-----|--------|
| [2026-11_label-checks](development%20reports/2026-11_label-checks.md) | website | DINOv3 fingerprint checks that flag labels looking unlike their species or like another one, for a second look in Media; 17 decisions |
| [2026-10_aws-reannz-migration](development%20reports/2026-10_aws-reannz-migration/README.md) | website + infra | Azure to AWS (Lambda, S3, SSM) with the GPU work as batches on the REANNZ cluster: what is built, the lease and results contracts, what is left, the migration steps and the production checklist; the design is its `approach-b.md` |
| [2026-10_ui-audit](development%20reports/2026-10_ui-audit/README.md) | website | UI audit of every page for role fit, scale (100 deployments, 50,000 photos) and clutter; eight decisions and a nine-agent improvement plan |
| [2026-10_e2e-in-ci](development%20reports/2026-10_e2e-in-ci.md) | website (CI) | The Playwright flows in CI: on each Cloudflare build of `dev`, on a stack started on the runner, nightly, and what pull request previews need |
| [species-brain-training-spec](development%20reports/species-brain-training-spec.md) | website | Media → Actions → *Create species ID model…*: the Edge Impulse recipe (dataset, transfer learning, int8 export, Vela) automated from a selection, with a dataset-ZIP fallback when no trainer is configured. Serves the GDMA APAC key result on non-technical users training a model |
| [2026-09_false-negatives-and-vlm-audit](development%20reports/2026-09_false-negatives-and-vlm-audit/README.md) | website + pipeline | Detector false negatives (SpeciesNet/MegaDetector), Motion ROI burst differencing, Wildlife Brain fallbacks, iNaturalist policy constraints, and (§6) the flag-gated Gemini animal-presence step with its labeller and evaluation script |
| [2026-09_person-detection-e2e](development%20reports/2026-09_person-detection-e2e/README.md) | website + firmware + mobile | The person detection model along the whole embedded path on real hardware, every stage checked: upload, `label_map`, transfer, capture, Camera AI reflection |
| [dual-layer-ai-architecture-proposal](development%20reports/dual-layer-ai-architecture-proposal.md) | all repos | Camera AI / Cloud AI / Wildlife Brain naming (canon now lives in [AI-ARCHITECTURE](resources/AI-ARCHITECTURE.md)), edge↔cloud integration framework, LoRaWAN alert-logic design (instant/digest/back-off), validation roadmap + user stories |
| [2026-09_evidence-pipeline-architecture](development%20reports/2026-09_evidence-pipeline-architecture/README.md) | website + ww-backend + firmware | Owns the evidence design: burst rules, evidence score v1, consensus row, `media_evidence` contract, prompt v2, stratified benchmark, cost per 100,000 frames; issue drafts for ww-backend and firmware |
| [model-class-semantics](development%20reports/model-class-semantics.md) | website + ww-backend | What a model class may assert (a taxon or a type, LM-10), and what that means for the person detector and a GBIF export |
| [ios-universal-links-team-id](development%20reports/ios-universal-links-team-id.md) | website | The wrong Apple Team ID in the app-site-association file that sent iOS password-reset links to Safari |
| [decoupled-upload-pipeline-spec](development%20reports/decoupled-upload-pipeline-spec.md) | ww-backend + website | Create `media` rows at ingest instead of inside the Drive job, + resumable backup sync, precheck/dedup and integrity audit, so a storage failure can't make photos vanish |
| [lorawan-alert-execution-spec](development%20reports/lorawan-alert-execution-spec.md) | website + backend + both firmwares | Build breakdown for `device_alert_rules` execution: manifest compile → Nordic strategy execution → Himax I2C score forward → backend uplink decode. Implements the proposal's §3 alert logic |
| [bmp-ingestion-analysis](development%20reports/bmp-ingestion-analysis.md) | website + firmware | Raw-BMP ingest + in-pipeline JPEG re-compress; device capture behaviour; open firmware ask: #1 same-frame dual-write |
| [dual-camera-rpi-analysis](development%20reports/dual-camera-rpi-analysis.md) | firmware/hardware | HM0360 (night/IR) ↔ Raspberry Pi (day/colour) camera swap + dual-write interaction |
| [exif-telemetry-firmware-spec](development%20reports/exif-telemetry-firmware-spec.md) | firmware | Add temperature/battery to EXIF via UserComment (smallest change) |
| [ww-backend-schema-handoff](development%20reports/ww-backend-schema-handoff.md) | ww-backend | Items for the schema/seed owners: the `qdrant_collection` rename, the seed's stale admin name, live GRANT/RLS parity, and the schema the storage-quota spec waits on |
| [storage-quota-spec](development%20reports/storage-quota-spec.md) | ww-backend + website | Per-org storage quota: `organisation_usage` schema + trigger + enforcement hook |
| [inaturalist-integration](development%20reports/inaturalist-integration.md) | website | iNaturalist publish + community-ID sync; the sync updates the badge only and writes no identifications (§6, 25 Oct 2026) |
| [gpu-worker-infra-spec](development%20reports/gpu-worker-infra-spec.md) | website (infra + CI/CD) | Design history of the ARQ GPU worker (⚠️ §3 and §7 were overridden during the build): Redis + ACA GPU + pgvector + KEDA; versioning, observability, batching, retries. **Operate from** [cloud-infrastructure](resources/cloud-infrastructure.md) + [prod-worker-provisioning-runbook](resources/prod-worker-provisioning-runbook.md) |

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
| [2026-09_gcp-native-model-training](development%20reports/_archive/2026-09_gcp-native-model-training/README.md) | Species-model training as a Cloud Run job (`MODEL_TRAINER=gcp`); retired, its code goes with [#463](https://github.com/wildlifeai/ww-website/issues/463) |
| [2026-09_gcp-pilot-and-migration](development%20reports/_archive/2026-09_gcp-pilot-and-migration/README.md) | The Azure to Google Cloud plan and its ML-worker pilot, superseded on 2026-11-22 by the AWS and REANNZ migration |
| [per-crop-classification-spec](development%20reports/_archive/per-crop-classification-spec.md) | Per-detection species classification (BioCLIP per crop), shipped behind `FF_PER_CROP_CLASSIFY_ENABLED` |
| [access-test-seed-spec](development%20reports/_archive/access-test-seed-spec.md) | The access-scenario seed deployments, shipped as ww-backend's `seeds/dev/access_test_deployments.sql` |

## Conventions

- **Living docs** (onboarding + resources) describe **current behaviour** and must be updated with the
  code. Nav/page names come from `App.tsx` `USER_TABS` — renaming a tab should trigger a doc grep for
  the old name.
- **Specs** (development reports, top level) are dated active hand-offs. When the work ships, drop a
  `🕰️ Historical snapshot` status banner and move the file into `_archive/`.
- Every development report carries a one-line **`> **Status:**`** banner under its title so currency is
  visible at a glance.
- The schema, RLS, and table GRANTs are owned by [`ww-backend`](https://github.com/wildlifeai/ww-backend), never documented here as editable.
