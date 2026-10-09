# AI Architecture — the three layers and their names

Canonical naming + integration map for Wildlife Watcher's AI. Detailed behaviour
stays in the docs each stage owns (linked below); the full rationale, LoRaWAN alert
spec, and validation roadmap live in the
[dual-layer AI architecture proposal](../development%20reports/dual-layer-ai-architecture-proposal.md).

## Naming (use these everywhere)

| Layer | Technical name | User-facing name | What it is |
|---|---|---|---|
| On-device inference | **Edge AI** | **Camera AI** | Custom int8 TFLite classifier (Edge Impulse), Vela-compiled for the Ethos-U55 NPU, running on the WW500's Himax HX6538 |
| Cloud inference | **Cloud AI** | **Cloud AI** | SpeciesNet ensemble + optional BioCLIP second opinion, auto-run on upload ([04-AI-PIPELINE](../onboarding/04-AI-PIPELINE.md)). The flag-gated **Gemini presence filter** (a hosted VLM asked "is there an animal?" per frame, `FF_GEMINI_PRESENCE_ENABLED`) is part of this layer: its rows are `ai_origin='cloud'` with `source_model_version` = the Gemini model id, so they sit beside SpeciesNet's, never over them |
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
| Detection threshold: OP 16 is a **logit 0–127**, not a percent — convert per the model's output quantization | firmware `cvapp.h`, manifest compile |
| Label chain: output tensor index *i* → `labels.txt` line *i* → `ai_models.label_map[label]` → taxon or type | convert job (LM-1/2 checks in `domain/model.py`), firmware, edge reflection (LM-10 in `domain/label_map.py`) |
| Edge results in the image: EXIF UserComment `0x9286` `"label: pct%; "` (+ DeploymentID `0xF200`, MakerNote `0x927C`) | firmware `exif_builder`, website `domain/exif.py` |

## How the layers combine

1. **Filter → verify:** Camera AI reacts in the field; Cloud AI re-judges every image
   on upload. Both results sit on the same media (`ai_origin` distinguishes them);
   canonical label precedence is human > cloud > edge.
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
| OP parameters (OP 14/15/16 …) | firmware `_Documentation/Operational_Parameters.md` |
| LoRaWAN production ingest (canonical) | ww-backend `documentation/resources/LORAWAN_INGEST.md` — the `lorawan-ingest` edge function. The website's [lorawan-webhook-setup](./lorawan-webhook-setup.md) documents a **legacy prototype** parser only |
