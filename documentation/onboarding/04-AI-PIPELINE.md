# 04 — AI Pipeline & Wildlife Brain

How AI annotations are produced. Two tracks: the **SpeciesNet inference pipeline** (detect +
classify) and the **Wildlife Brain** (DINOv3 embeddings → clustering → active learning).

> **This is the *cloud* AI pipeline** — server-side models that run when images are uploaded
> to the website. It is distinct from the **on-device (embedded) model** that runs on the camera
> itself; for that — custom model upload, conversion, deployment, and how its predictions return
> via EXIF — see [AI Model Pipeline](../resources/ai-model-pipeline.md) and the
> [Embedded Model Lifecycle](../resources/embedded-model-lifecycle.md). The two are complementary:
> a given image can carry both an on-device prediction and a cloud SpeciesNet result.

> **Running it locally** (the heavy ML deps live in the `dev` Docker image, feature flags, model
> weights, HF token) → see [Running the AI/ML pipeline locally](../../readme.md#running-the-aiml-pipeline-locally).

> **Where it runs in the cloud.** Everything below executes in a **dedicated ARQ GPU worker**
> (`--target worker`, serverless T4), *not* in the API container — the lean `--target api` image has no
> ML deps. The API only enqueues. So **AI detection is off on any backend without `REDIS_URL` + a
> deployed worker**: dev has both (since 2026-07-03), production does not yet. Flags must be set on the
> **worker**, not just the API. See [cloud-infrastructure](../resources/cloud-infrastructure.md) and
> [prod-worker-provisioning-runbook](../resources/prod-worker-provisioning-runbook.md).

## SpeciesNet inference pipeline

Lives in [`backend/app/domain/pipeline.py`](../../backend/app/domain/pipeline.py), triggered by
`POST /api/pipeline/run` and **auto-run (async) at the end of every image upload** via
`auto_annotate_deployments` (gated by `FF_ML_ENABLED` + `FF_PIPELINE_ENABLED`; the step set is
built from the enabled per-step flags). Steps run in order:

| Step (`PipelineStepType`) | What it does | Flag |
|---|---|---|
| `MEDIA_PREP` | Generate thumbnail + preview renditions and upload them to the **public Supabase Storage bucket** (`media-renditions`), recording the URLs on `media_assets` so the grid never hits Google Drive. Originals stay in Drive. No observations. Each photo is tried three times (1 s, then 2 s apart); a permission error (`42501`) is logged once as `media_prep_refused` and stops the step, since every other photo would be refused too. Photos left without a thumbnail are made later by the backfill (`POST /api/media/thumbnails/{deployment_id}`, the grid's **Retry**). | `FF_MEDIA_REGISTRY_ENABLED` |
| `GEMINI_PRESENCE` | Asks Gemini whether each frame holds an animal and writes its own `animal`/`blank` row beside SpeciesNet's, with prompt v1 (`PROMPT_VERSION` in `backend/app/services/gemini_presence.py`), because on the labelled set v2 matched v1's wildlife recall (98.8% vs 98.1% on 632 frames) at $0.35 vs $0.21 per 1,000 frames and called 68 of 82 person frames an animal; v2 and v3 stay selectable for `backend/scripts/eval_presence.py` ([false-negatives report §6](../development%20reports/2026-09_false-negatives-and-vlm-audit/README.md)). | `FF_GEMINI_PRESENCE_ENABLED` |
| `SPECIESNET` | **The core model.** Resolves each image to a temp file, runs the **SpeciesNet ensemble** (detector + species classifier in one pass), and writes media-level `observations` with bbox, detection `confidence`, species `classification_probability`, and `scientific_name`/`vernacular_name`. By default SpeciesNet classifies **one species per image**, so multiple detection boxes of the same type are collapsed into **one** observation carrying a `count` (number of boxes) + the highest-confidence box as the representative bbox — not N duplicate rows. **With `FF_PER_CROP_CLASSIFY_ENABLED` on** this collapse is skipped: each kept detection becomes its own observation (`count = 1`, its own bbox), refined per crop by the classify stage — see [Per-detection classification](#per-detection-classification-ff_per_crop_classify_enabled) below. The species is **taxonomically rolled up** by confidence (`rollup_taxon`): below `SPECIES_CONFIDENCE` (0.5) it backs off to genus, below `GENUS_CONFIDENCE` (0.35) to the most specific available higher rank — so a shaky 0.4 "Apteryx mantelli" is recorded as "Apteryx", not a false species claim. | `FF_SPECIESNET_ENABLED` |
| `ANIMAL_CROP` | Crops the best animal detection into `media_assets.animal_crop_url` for DINOv3 / BioCLIP. No observations. | — |
| `BIOCLIP` | **Classify stage — pluggable.** Runs a classifier on the animal crop and adds a *second* `animal` observation tagged with the classifier's `source_model_version` — a complement / second opinion to SpeciesNet, strong for taxa outside SpeciesNet's ~2k label set. The classifier is resolved from a registry (`domain/classifiers.py`): [Imageomics BioCLIP](https://imageomics.github.io/pybioclip/) (`bioclip-2`) by default, or whatever `config['classifier']` selects. | `FF_BIOCLIP_ENABLED` |

### Detector → Crop → Classify (the inference tree)

The pipeline is a decision tree — **detect** (where are the animals?) → **crop** → **classify**
(what species?).

**Detection is global.** Every photo, in every project, is detected by the same SpeciesNet ensemble
(the `SPECIESNET` step). There is **no per-project detector** — finding the animal and filtering
blanks works the same everywhere.

**Classification is global by default, with an optional per-project override.** The default classify
path is also the same for everyone: SpeciesNet's own species guess, plus an optional BioCLIP second
opinion. What's pluggable is *only* the classify stage: it's a swappable contract
(`domain/classifiers.py`) where a `Classifier` takes animal-crop paths and returns one
`ClassifierResult` each, resolved from a registry by id at run time. Today only `bioclip` is
registered, so nothing changes unless you opt in. The point of the seam is that a project working on
taxa the general models handle poorly (e.g. NZ geckos/wētā) *could* register a **custom species
model** and select it via `config['classifier']` — without touching detection or the rest of the
pipeline. A lighter-weight alternative is constraining BioCLIP to a custom label set with
`bioclip_labels`. `ClassifierResult` is field-compatible with BioCLIP's `CropPrediction`, so any
classifier flows through the same observation builder unchanged.

> **Status:** the classifier registry + routing is wired and tested; per-project selection currently
> rides the `config['classifier']` run override, and persisting the choice on the project row is the
> remaining wiring. A parallel `Detector` contract (so detection could also vary) is a *possible*
> future step, **not** something in use — detection stays global.

### Per-detection classification (`FF_PER_CROP_CLASSIFY_ENABLED`)

A frame holding a cat *and* a rat gets **one** species by default, because SpeciesNet's classifier is
image-level even though its detector is multi-box. With the flag on, the collapse in
`build_speciesnet_observations` is skipped and BioCLIP classifies **each crop**, so every animal gets
its own species:

```
SpeciesNet DETECT → one observation per detection (its own species, count=1)
  → generate_observation_crops → crop_url per observation
  → classify_crops (BioCLIP per crop) → one BioCLIP row per detection, on the same bbox
  → DINOv3 embeddings on crops → clustering / similarity
```

The BioCLIP row sits **beside** the SpeciesNet row and never edits it, so each model keeps its own
verdict (#162); it carries the detection's box and confidence and no `crop_url`
(`build_crop_classification_observation`). The photo viewer draws a shared box once and lists both
labels (`groupByBox` in `frontend/src/lib/observations.ts`). Below-threshold crops add no row. With the
flag on, `count` becomes **human-only** ("N individuals" annotations) since AI rows are 1-per-detection.
Requires the GPU worker; set it on the **worker**, and reprocess a deployment to migrate existing rows
(`delete_superseded_ai_observations` makes re-runs replace, not append). Design detail:
[per-crop-classification-spec](../development%20reports/per-crop-classification-spec.md).

**Idempotent + incremental (Guard 2):** by default `run_pipeline(only_unannotated=True)` fetches only
media that **don't already have a cloud `source_type='ai'` observation**, so re-running (or
re-uploading) a deployment processes only the *new* images. Camera AI rows (`ai_origin='edge'`)
don't count: they are reflected before the pipeline runs, on the upload job and in
`auto_annotate_deployments` alike (#161). The manual endpoint accepts `only_unannotated=false`
to force a full re-run. Each run records an `annotation_runs` row (steps, threshold, observation
count, `created_by`) for provenance. Every read pages past PostgREST's 1,000-row cap.

**A human verdict is final (#284).** A photo with a live `human_reviewed`, `expert_reviewed` or
`consensus_approved` row is skipped by every run except a `force` one, and each step that writes
rows (Gemini, SpeciesNet, BioCLIP, evidence fusion) checks again just before it writes
(`without_human_verdicts`), so a photo reviewed while the model was working gets no machine row
beside the verdict. A `force` run (CamtrapDP import) keeps the verdict and adds no row of the model
whose own row the reviewer ruled on. Nothing spans the check and the insert, so a review landing in
those milliseconds can still race.

**One run per deployment at a time (#284).** `run_pipeline` holds a per-deployment lock
(`services/locks.py`) while it reads the media and runs the steps, so a second run waits and then
sees what the first one wrote. With `REDIS_URL` set the lock is a Redis key with a renewed 10-minute
TTL and spans the API workers and every ARQ worker; without Redis, or when Redis is unreachable, it
is an in-process lock only. `POST /api/pipeline/run` does not wait: it returns `PIPELINE_BUSY`.
The upload job joins a **queued** `ai_pipeline` job on the deployment (under a lock, so chunks that
arrive together make one); the job stays queued while it is deferred and while it waits for the
lock, and turns `processing` only when it reads its media. A deployment whose job is already
`processing` gets one follow-up job, which later chunks join. A follow-up's wait counts toward the
ARQ `job_timeout` (1 hour).

> **History:** the earlier `MegaDetectorStep`, `SpeciesNetClassifierStub`, and `EmptyFrameStep`
> placeholders were **removed** — the SpeciesNet ensemble subsumes detection, classification, and
> blank handling. The registry is now `MEDIA_PREP → SPECIESNET → ANIMAL_CROP → BIOCLIP`.

### How blank / empty images are handled

A blank frame is a **positive result, not missing data**. When SpeciesNet keeps no detections above
the confidence threshold, it writes **one observation with `observation_type='blank'`** (no bbox,
`source_type='ai'`, `review_status='ai_reviewed'`). Two more rules drop boxes first, because the
detector answers empty night scenes with a whole-frame "vehicle" (#285): a box over
`SPECIESNET_WHOLE_FRAME_AREA` (0.9) of the frame needs `SPECIESNET_WHOLE_FRAME_MIN_CONFIDENCE` (0.5)
whatever its class, and `SPECIESNET_DROP_VEHICLES` (on) drops every vehicle. The run's config can
override each; the `annotation_runs` row and the evidence fusion audit comment record the values
used, and a box these rules drop adds nothing to the fusion score. The distinction:

- **Blank** = processed, no animal → has a `blank` AI observation → shows the teal **AI** badge.
- **Unprocessed** = no observations at all → shows the neutral grey **⧗ Processing** badge (still
  working through the pipeline). The red **✕ Issue** badge is reserved for an explicit pipeline
  error (see `frontend/src/components/ui/StatusBadge.tsx`).

Blanks are excluded from species charts but counted in the observation-type breakdown and the
deployment **false-trigger rate**.

**Which verdict a photo shows (#170).** The Annotations card's Empty or species label and the
detection notifications read a human verdict first, then the evidence fusion consensus row
(`source_type='consensus'`), then the per-model rows as before: `photoVerdict` in
`frontend/src/lib/observations.ts` and `photo_detections` in `backend/app/services/notifications_service.py`.
A consensus animal no model named is notified as "Unidentified animal".

## Wildlife Brain (embeddings → clustering → active learning)

A second, deeper track (`domain/wildlife_brain.py`, `embedding_lifecycle.py`, `clustering.py`,
`active_learning.py`), surfaced through the `/api/brain/*`, `/api/intelligence/*`, and `/api/qa/*`
routers. Gated by `FF_WILDLIFE_BRAIN_ENABLED` / `FF_ACTIVE_LEARNING_ENABLED`.

```
animal crop ──DINOv3──▶ media_embeddings ──HDBSCAN──▶ clusters (+ outliers)
                                   │
                                   ▼
   active_learning_score = f(novelty, uncertainty, disagreement, is_outlier)
                                   │
                                   ▼
        review queue (highest-value images first) + QA agreement report
```

- **Embeddings**: DINOv3 vectors per animal crop → `media_embeddings` / `embedding_runs`. The vector
  store is **`pgvector` in Supabase** — vectors live in `media_embeddings.embedding`, searched via the
  `match_media_embeddings` RPC; the former Qdrant container has been **removed** (see
  [deployment guide → Vector Store](../resources/deployment-guide.md#vector-store--pgvector-supabase)).
  **Auto-run:** after the annotation pipeline finishes, `auto_annotate_deployments` chains
  `auto_embed_deployment` (gated on `FF_WILDLIFE_BRAIN_ENABLED`), so embeddings/clusters exist
  without a manual `POST /api/brain/embed/{id}` trigger.
- **Clustering**: HDBSCAN groups visually similar crops; outliers flagged. The Annotations grid's
  **Group by → Cluster** reads the `media_id → cluster_id` map from `POST /api/brain/clusters/multi`;
  clusters are confirmed in `ClusterReviewPage` (`/clusters/:id`), which bulk-writes labels with
  cluster provenance. For small deployments the HDBSCAN `min_cluster_size` is scaled down to the
  dataset (`prepare_cluster_input` / `cluster_hdbscan`) so they still form real clusters instead of
  collapsing into one group.
- **Active learning**: `get_review_queue()` ranks media by `active_learning_score` (novelty =
  `1 − cluster_confidence`) → surfaced in `ReviewQueuePage` (`/review/:id`).
- **QA**: `qa_report()` computes AI-vs-human agreement (a precision proxy over images carrying both
  an AI and a human label) → shown on `DatasetHealthPage` (`/intelligence/:id`) via `useProjectQa`.

## Observation data model (shared by both tracks)

| Field | Values | Purpose |
|-------|--------|---------|
| `source_type` | `ai · human · imported · consensus` | who originated the row |
| `review_status` | `unreviewed · ai_reviewed · human_reviewed · expert_reviewed · consensus_approved` | validation lifecycle |
| `classification_method` | `human · machine` | who authored the label |
| `confidence`, `classification_probability` | 0–1 | detection vs classification certainty |
| `bbox_x/y/w/h` | 0–1 | normalised box (media-level only) |
| `embedding_run_id`, `cluster_id` | FK / int | deep provenance back to the Brain run |
| `taxon_id` | FK → `taxa` | canonical taxonomy link |

Human review is recorded via `frontend/src/lib/observations.ts` — see
[05-ANNOTATION-WORKFLOW](./05-ANNOTATION-WORKFLOW.md).

## Model conversion (separate)

Edge Impulse model ZIPs are converted for the camera's Ethos-U NPU via the **Vela** CLI
(`services/vela.py`, `domain/model.py`, `POST /api/models/convert`) and registered in `ai_models`.
See [AI Model Pipeline](../resources/ai-model-pipeline.md).
