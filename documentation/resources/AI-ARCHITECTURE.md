# AI Architecture — the three layers and their names

Canonical naming + integration map for Wildlife Watcher's AI. Detailed behaviour
stays in the docs each stage owns (linked below); the full rationale, LoRaWAN alert
spec, and validation roadmap live in the
[dual-layer AI architecture proposal](../development%20reports/dual-layer-ai-architecture-proposal.md).

## Naming (use these everywhere)

| Layer | Technical name | User-facing name | What it is |
|---|---|---|---|
| On-device inference | **Edge AI** | **Camera AI** | Custom int8 TFLite classifier (Edge Impulse), Vela-compiled for the Ethos-U55 NPU, running on the WW500's Himax HX6538 |
| Cloud inference | **Cloud AI** | **Cloud AI** | SpeciesNet ensemble + optional BioCLIP second opinion, auto-run after upload, on AWS in the next REANNZ batch ([04-AI-PIPELINE](../onboarding/04-AI-PIPELINE.md)). The flag-gated **Gemini presence filter** (a hosted VLM asked "is there an animal?" per frame, `FF_GEMINI_PRESENCE_ENABLED`) is part of this layer: its rows are `ai_origin='cloud'` with `source_model_version` = the Gemini model id, so they sit beside SpeciesNet's, never over them |
| Embedding intelligence | **Wildlife Brain** | **Wildlife Brain** | DINOv3 embeddings → clustering → active learning / QA / conservation intelligence |

- **Species Brain** *(user-facing)* = one deployed edge model version (an `ai_models`
  row), e.g. "Rat Detection v10" — the thing a project assigns and the manifest installs.
  Never call the other layers "brains".
- Observation provenance: `source_type='ai'` + **`ai_origin`** = `edge` | `cloud`
  (ww-backend `observations` column, `dual_ai_v0` — merged), with
  `source_model_version` = `'{firmware_model_id}V{version_number}'` for edge rows —
  the same identity that names the `.TFL` file. Promotion to staging/prod:
  [dual-ai-production-rollout](./dual-ai-production-rollout.md).

## The contracts that must never break

| Contract | Where |
|---|---|
| Model identity: `firmware_model_id` (OP 14) + `version_number` (OP 15) → `{id}V{ver}.TFL` | `ai_model_families` / `ai_models`, manifest, firmware |
| Detection threshold, OP 16 (`MODEL_THRESHOLD`), checked 2026-11-26. The firmware's `main` and `dev` compare it with the target class's raw int8 output: **0 to 127, default 18** (about 57%). With [Seeed PR 289](https://github.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/pull/289) (open) it becomes a **percent from 0 to 100, default 60**, meaning the same for every model. The mobile app writes the project's `detection_threshold_pct` (50 to 99, default 57) as is ([ww-mobile-app#524](https://github.com/wildlifeai/ww-mobile-app/pull/524), on its `dev`), which firmware before PR 289 reads as about 72% for 57. Prepare SD card copies `CONFIG.TXT`, and so OP 16's default, from the firmware's `main` | firmware `cvapp.h` and `MANIFEST/config_file.md`; the mobile app at deployment; ww-backend `projects.detection_threshold_pct` |
| Label chain: output tensor index *i* → `labels.txt` line *i* → `ai_models.label_map[label]` → taxon or type | convert job (LM-1/2 checks in `domain/model.py`), firmware, edge reflection (LM-10 in `domain/label_map.py`) |
| Edge results in the image: EXIF UserComment `0x9286` `"label: pct%; "` (+ DeploymentID `0xF200`, MakerNote `0x927C`) | firmware `exif_builder`, website `domain/exif.py` |

## How the layers combine

1. **Filter → verify:** Camera AI reacts in the field; Cloud AI re-judges every image
   on upload. Both results sit on the same media (`ai_origin` distinguishes them); the
   label a photo shows is the human verdict, then the evidence fusion consensus row, then
   the per-model rows ([04-AI-PIPELINE](../onboarding/04-AI-PIPELINE.md), "Which verdict a photo shows").
2. **Agreement telemetry:** per-edge-model-version precision vs Cloud AI + human review
   — the trust dial for alert thresholds.
3. **Retraining flywheel:** reviewed observations export as Edge Impulse training data
   → new version in the same family → redeploy.
4. **Alert verification:** LoRaWAN alerts (in development — see the proposal §3 and
   ww-backend `device_alert_rules`) are provisional claims, confirmed/refuted when the
   SD card is uploaded.

## Where each stage is documented

| Stage | Doc |
|---|---|
| Edge model end-to-end map (train → deploy → EXIF → reflect) | [embedded-model-lifecycle](./embedded-model-lifecycle.md) |
| Upload / Vela convert / manifest detail | [ai-model-pipeline](./ai-model-pipeline.md) |
| Cloud pipeline + Wildlife Brain | [04-AI-PIPELINE](../onboarding/04-AI-PIPELINE.md) |
| Schema (`ai_model_families`, `ai_models`, `observations`, `device_alert_rules`) | ww-backend `documentation/resources/DATABASE_REFERENCE.md` |
| OP parameters (OP 14/15/16 …) | firmware `EPII_CM55M_APP_S/app/ww_projects/ww500_md/MANIFEST/config_file.md`, the canonical copy since 2026-09-04 (`_Documentation/Operational_Parameters.md` now points there) |
| LoRaWAN ingest | ww-backend [LORAWAN_INGEST](https://github.com/wildlifeai/ww-backend/blob/dev/documentation/resources/LORAWAN_INGEST.md), the `lorawan-ingest` edge function |
