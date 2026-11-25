# Spec — Per-detection (per-crop) species classification

> **Status:** 🕰️ Historical snapshot, archived 2026-11-26. Shipped: one observation per detection
> with BioCLIP per crop (`build_speciesnet_observations` with `per_detection`, `backend/app/domain/pipeline.py`),
> behind `FF_PER_CROP_CLASSIFY_ENABLED`, on in production, whose GPU worker runs since 2026-07-26.
> What the code does today: [04-AI-PIPELINE](../../onboarding/04-AI-PIPELINE.md). Below is the spec as
> written, kept for the why.

**Goal:** label **each detected animal** in a frame independently, so mixed-species frames
(e.g. a cat *and* a rat) and same-species groups get accurate per-animal species — replacing
today's *one species per image* behaviour.

## Why
SpeciesNet is a **multi-box detector (MegaDetector) + an image-level classifier**. The detector
finds every animal, but the classifier returns a single species for the whole frame. So a
cat+rat frame is labelled as one species (or, when uncertain, a rolled-up taxon), never split.

## Design at a glance
The plumbing already exists — this is mostly *"stop collapsing"* + *"classify each crop."*

| Piece | Where | Status |
|-------|-------|--------|
| Per-box detections (bbox, category, confidence) | `app/services/speciesnet_service.py` (`Detection`, `ImagePrediction`) | ✅ exists |
| One crop per detection → `observations.crop_url` | `app/domain/media_registry.py::generate_observation_crops` | ✅ exists |
| Per-crop zero-shot classifier (full taxonomy) | `app/services/bioclip_service.py` (BioCLIP TreeOfLife) | ✅ exists |
| Per-row `bbox_*`, `crop_url`, `scientific_name`, `vernacular_name`, `classification_probability` | `observations` table (ww-backend) | ✅ exists |
| **One observation per detection** | `app/domain/pipeline.py::build_speciesnet_observations` | ❌ **collapses today** |
| **`classify_crops` step (BioCLIP per crop)** | new step in `app/domain/pipeline.py` | ❌ **new** |

## Changes (ordered)

### 1. De-collapse — one observation per detection
`build_speciesnet_observations` currently groups detections by `observation_type` and emits one
row per type with `count = len(dets)` and the image-level species. Change it to emit **one row
per kept detection** (`count = 1`, its own bbox). The image-level SpeciesNet species becomes a
**provisional** label on each `animal` row, refined in step 3. Blank case unchanged.
`delete_superseded_ai_observations` already makes re-runs replace-not-append (keyed by
media + `source_model_version`), so reprocessing migrates a deployment cleanly.

### 2. Crop per observation — *already works*
With step 1, each detection has its own observation, so `generate_observation_crops` (which
iterates `observation_type='animal'` rows and writes each bbox crop) produces a distinct
`crop_url` per animal. No change beyond verifying coverage.

### 3. `classify_crops` — BioCLIP per crop (NEW)
After cropping, for each animal observation with a `crop_url`, run BioCLIP on **that single crop**
and write *that row's* species:
```
for obs in animal_observations(media):
    pred = bioclip.classify(fetch(obs.crop_url), rank=settings.BIOCLIP_RANK)
    obs.{scientific_name, vernacular_name, classification_probability, classified_by} = pred…
```
Keep `rollup_taxon` as the per-crop fallback (low score → coarse taxon, never a confidently-wrong
species). Optionally retain the SpeciesNet image-level guess for an agreement signal.

### New pipeline order
```
1. SpeciesNet DETECT      → one observation per detection (provisional species, count=1)
2. generate_observation_crops → crop_url per observation
3. classify_crops (BioCLIP per crop) → per-observation species          ← NEW
4. (existing) DINOv3 embeddings on crops → clustering / similarity
```

## Schema (ww-backend)
Already sufficient (per-row bbox, `crop_url`, species, `classification_probability`). Optional:
- `observations.classifier_source` (`speciesnet_image` | `bioclip_crop`) for provenance — or reuse
  `classified_by` / `source_model_version`.
- Reserve `count` for **human** "N individuals" annotations now that AI rows are 1-per-detection.

## Rollout
- Behind a flag **`FF_PER_CROP_CLASSIFY_ENABLED`** (default off). Flip + reprocess a deployment to migrate.
- **UI: no work** — the Labels/crop view already renders per-detection observations.

## Cost & infra
- N classifier passes per frame instead of 1 (typically 1–3 animals → modest). **Batch crops per
  frame** for GPU efficiency; cap detections-classified-per-frame to bound busy frames.
- BioCLIP + DINOv3 are GPU jobs → **require the ARQ GPU worker**. That worker is **live on dev**
  (`ww-embedding-worker-dev`, serverless T4) and already runs per-crop BioCLIP; **production is still
  API-only** and needs the prod worker provisioned before the flag can be flipped there.
- **Versioning & batching are owned by the infra spec** — each per-crop observation already carries its
  classifier version in `source_model_version` / `classified_by`, but the per-job model manifest, the
  `embedding_model` **read filter** (a `WHERE` predicate in pgvector, so a BioCLIP/DINOv3 bump can't
  contaminate clusters), the cross-frame crop **batching window**, and the retry/DLQ policy all live in
  [gpu-worker-infra-spec.md §9–§12](gpu-worker-infra-spec.md#9-model--embedding-versioning-cluster-consistency).
  Flip `FF_PER_CROP_CLASSIFY_ENABLED` only on a worker that already has those in place.

## Documentation updates required (when this lands)
- ✅ **[onboarding/04-AI-PIPELINE.md](../onboarding/04-AI-PIPELINE.md)** — the flag-on behaviour and the
  4-stage order are documented under *Per-detection classification*.
- ✅ **[onboarding/05-ANNOTATION-WORKFLOW.md](../onboarding/05-ANNOTATION-WORKFLOW.md)** — notes that AI
  yields one observation per animal and `count` becomes human-only.
- ✅ **[resources/deployment-guide.md](../resources/deployment-guide.md)** — `FF_PER_CROP_CLASSIFY_ENABLED`
  is in the AI-pipeline checklist row.
- ⬜ **[resources/ai-model-pipeline.md](../resources/ai-model-pipeline.md)** — add BioCLIP-per-crop to the model flow.
- This spec → move to `_archive/` once shipped to production.

## Legacy code to remove / replace
- **`build_speciesnet_observations` collapse block** ([pipeline.py:267-297](../../backend/app/domain/pipeline.py)) —
  remove the `by_type` grouping + `count = len(dets)` + single image-level species application. Replace
  with one-row-per-detection. Update the docstring (drop "classifies one species per image … collapsed
  into one").
- Any consumer assuming **one AI animal observation per image** — re-check the grid's `count` badge
  (now human-only) and `generate_observation_crops`' "hero crop" selection (still fine, but the hero is
  now one of N rather than the only row).
- If a current BioCLIP step re-classifies only the **hero** crop, remove it in favour of the per-crop
  `classify_crops` loop (don't run both).

## Testing
- Unit: `build_speciesnet_observations` — N detections ⇒ N rows (not collapsed); blank unchanged.
- Fixture: a cat+rat frame ⇒ **2 observations with distinct species** after step 3.
- Integration: each observation has its own `crop_url`; BioCLIP writes per-row species; reprocess
  replaces (no dup rows) via `delete_superseded_ai_observations`.

## Effort
| Item | Est. |
|------|------|
| 1. De-collapse + unit tests | ~0.5 day |
| 3. `classify_crops` step (BioCLIP per crop) + apply/override + tests | ~1–2 days |
| GPU worker (prerequisite) | separate — see worker infra spec |

## Notes / decisions
- **BioCLIP over re-running SpeciesNet's classifier per crop:** BioCLIP is zero-shot across the whole
  taxonomy and is *designed* for crops; SpeciesNet's classifier is image-level and would need N passes
  on sub-images. Use SpeciesNet's image classification only as a fast prior / "is there an animal" gate.
- **Fail-safe labelling:** an uncertain crop rolls up to a coarse taxon rather than a confidently-wrong
  species — preserves today's safe-degradation behaviour, now per animal.
