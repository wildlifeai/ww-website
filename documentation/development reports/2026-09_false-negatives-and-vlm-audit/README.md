# Detector False Negatives, Wildlife Brain Fallbacks, and VLM Auditing

> **Status:** 🔧 Active · 2026-10-09 · Web Platform & AI Pipeline. Sections 1 to 5 are the 2026-09-09 analysis; section 6 is the implementation on this branch (Gemini presence step, labeller, evaluation script), flag-gated. 700 frames are labelled; prompt v1 gives 98.1% recall at $0.21 per 1,000 frames and stays the production prompt, since v2 adds nothing on wildlife at $0.35 and calls people animals (section 6.5); v3 is opt-in.

This report analyses what happens when object detection models (MegaDetector / SpeciesNet) fail to detect animals (false negatives), details the existing fallbacks across the Wildlife Watcher architecture, and evaluates how Vision-Language Models (VLMs like PaliGemma, Gemma 3, and GPT-4o/Omni) and iNaturalist can be used for false-negative recovery.

---

## Executive Summary

1. **Current Pipeline Reality:** MegaDetector was subsumed into the cloud **SpeciesNet ensemble** (`domain/pipeline.py`). When an animal is undetected, the pipeline writes a single observation row with `observation_type = 'blank'` and `review_status = 'ai_reviewed'`. By default, no crop is generated.
2. **Existing Platform Fallbacks:**
   * **Motion ROI Fallback (`domain/motion_roi.py`):** Pure NumPy frame-differencing across bursts isolates moving regions even when the detector fails.
   * **Dual AI Disagreement (`domain/edge_reflection.py`):** Compares on-device Camera AI (`ai_origin='edge'`) with Cloud AI (`ai_origin='cloud'`) to surface missed animals.
   * **Wildlife Brain (`domain/wildlife_brain.py` & `domain/active_learning.py`):** DINOv3 feature embeddings and HDBSCAN density clustering identify outliers and rank uncertain or conflicting frames into the active learning review queue.
3. **Auditing with Modern VLMs (Gemma, GPT-4o / Omni, Qwen-VL):**
   * Multimodal foundation models excel at open-world reasoning, camouflage detection, and partial-occlusion detection where rigid bounding-box detectors stumble.
   * Because running VLMs across all raw images is cost-prohibitive, we propose a **Cascaded Blank Audit Pipeline** that routes suspicious `blank` frames (e.g. burst-isolated blanks or edge-cloud disagreements) to lightweight or frontier VLMs.
4. **iNaturalist Role & Constraints:**
   * iNaturalist's Computer Vision model is a **fine-grained species classifier**, not an object detector or blank-finder.
   * iNaturalist policies strictly forbid bulk unverified camera trap uploads or empty frames. Wildlife Watcher's iNaturalist integration (`inaturalist-integration.md`) explicitly drops blanks via a by-catch filter. iNaturalist is valuable only downstream for human taxonomic consensus on confirmed animal crops.

---

## 1. The Anatomy of False Negatives in Camera Traps

In camera trap monitoring, false negatives (missed animals) generally occur due to:

* **Severe Camouflage:** Animal pelage closely matching substrate, rocks, leaf litter, or grass.
* **Partial Occlusion / Boundary Entrances:** Only a tail, ear, snout, or leg entering the camera frame edge.
* **Nocturnal / IR Blur:** Low contrast, infrared glare, or motion blur during fast nocturnal passes.
* **Scale Extremes:** Very small animals (e.g. small rodents, insects, lizards) captured by cameras positioned high for large mammals.
* **Adverse Weather / Optical Artifacts:** Rain, condensation, dust on the lens, or direct sun flare blinding the detector.

### How the Cloud Pipeline Processes Undetected Frames

In `backend/app/domain/pipeline.py`:

```python
kept = [d for d in prediction.detections if d.confidence >= confidence_threshold]
if not kept:
    return [{**base, "id": str(uuid.uuid4()), "observation_type": "blank"}]
```

When no detection clears `confidence_threshold`:
* A single observation is committed with `observation_type = "blank"`, `classification_method = "machine"`, and `review_status = "ai_reviewed"`.
* The `ANIMAL_CROP` step (`AnimalCropStep`) relies on `generate_observation_crops(m["id"])`, which checks for bounding boxes. If none exist, no animal crop is generated, preventing downstream classifiers (like BioCLIP) from running.

---

## 2. Existing Platform Mitigations in Wildlife Watcher

Wildlife Watcher is not solely reliant on a single detector pass. Several architectural layers mitigate missed detections:

```
                      [ Image Upload ]
                             │
                  [ SpeciesNet Ensemble ]
                             │
                ┌────────────┴────────────┐
         [ Detection Found ]        [ No Detection ]
                │                         │
         Crop Observation           'blank' Observation
                │                         │
                ▼                         ▼
         [ Normal Pipeline ]       1. Motion ROI Burst Check (motion_roi.py)
         (DINOv3 / BioCLIP)        2. Edge AI Reflection (edge_reflection.py)
                                   3. Active Learning Queue (active_learning.py)
```

### 2.1 Motion ROI Burst Differencing (`domain/motion_roi.py`)
Wildlife Watcher cameras (WW500) capture bursts of frames upon PIR triggering. 
* Under `FF_MOTION_ROI_FALLBACK_ENABLED`, frames lacking a SpeciesNet detection are analyzed using pure NumPy/Pillow frame differencing (`compute_motion_roi`).
* By calculating the absolute difference between burst frames, morphological opening/closing, and connected-component bounding, it isolates what *moved* in the frame.
* This generates a synthetic crop for `media_assets.animal_crop_url`, allowing DINOv3 to extract visual features even without a neural detection box.

### 2.2 Dual-Layer AI Agreement (`domain/edge_reflection.py`)
Wildlife Watcher incorporates a two-layer AI architecture:
* **Edge AI (Camera AI):** An int8 TFLite classifier on the WW500's Himax HX6538 NPU embeds on-device detection probabilities into EXIF `UserComment` (e.g. `"rat: 87%; not rat: 13%;"`).
* **Cloud AI:** SpeciesNet running on the cloud GPU worker.
* When Edge AI detects an animal above `DEFAULT_REFLECT_THRESHOLD_PCT` (e.g. 50%) but Cloud AI records `blank`, the disparity is captured as an agreement conflict.

### 2.3 Wildlife Brain & Active Learning Ranking (`domain/active_learning.py`)
The platform does not treat all blanks equally. Unreviewed media are prioritized by an Active Learning score:

$$\text{AL Score} = 0.35 \cdot \text{novelty} + 0.35 \cdot \text{uncertainty} + 0.20 \cdot \text{disagreement} + 0.10 \cdot \text{outlier}$$

* **Disagreement ($w=0.20$):** High when Edge AI $\neq$ Cloud AI (e.g. Edge detected rat, Cloud detected blank).
* **Uncertainty ($w=0.35$):** High when detector confidence was borderline near the cutoff threshold.
* **Outlier ($w=0.10$):** High when DINOv3 visual embeddings place the crop or frame outside normal background clusters in HDBSCAN.

---

## 3. Foundation Models & VLMs for False-Negative Recovery

Modern multimodal foundation models (Vision-Language Models / VLMs) provide capabilities that traditional YOLO/SSD-style bounding-box detectors lack.

### 3.1 Model Capabilities Comparison

| Model Type | Examples | Strengths | Limitations | Role in Camera Traps |
|---|---|---|---|---|
| **Traditional Detectors** | MegaDetector v5, YOLOv8/v11, SpeciesNet detector | Fast (5–30 ms), cheap, tight bounding boxes | Rigid priors, misses camouflaged/partial animals, fixed label ontology | Primary front-line filter (processes 100% of images) |
| **Open-Source Grounded VLMs** | Google PaliGemma 2, Gemma 3, Qwen2.5-VL | Coordinates/bounding box grounding, strong semantic understanding, self-hostable | Higher inference latency (~200–800 ms/img), requires GPU | Targeted batch auditor for suspected blanks |
| **Frontier Multimodal LLMs** | OpenAI GPT-4o ("Omni"), Claude 3.5 Sonnet, Gemini 1.5/2.0 Pro | Reasoning over cryptic clues (eye reflections, subtle posture, blurry extremities) | API costs, rate limits, latency (~1–3 s/img) | Sampled quality assurance & calibration |
| **Self-Supervised Vision Encoders** | DINOv2, DINOv3 (Wildlife Brain) | Unsupervised feature representation, background/foreground clustering | No text explanation, requires clustering/linear head | Anomaly detection across entire image sets |

### 3.2 Prompting VLMs for False-Negative Recovery
When auditing frames flagged as `blank`, standard classification prompts are ineffective. Grounding prompts should specifically target detection edge cases:

```json
{
  "prompt": "Examine this camera trap image that was automatically flagged as empty. Inspect foliage, shadows, ground textures, and frame borders for subtle wildlife evidence (e.g., reflective eye shine, partial tail/snout/ear, camouflaged fur/feathers, or motion blur). Return JSON with fields: has_animal (bool), confidence (0.0-1.0), description (str), and estimated_bbox [ymin, xmin, ymax, xmax] if visible."
}
```

### 3.3 Proposed Architecture: The Cascaded "Blank Audit" Pipeline

Running frontier models on every single camera trap image is financially impractical (a single project can generate 50,000–500,000 photos per deployment). We propose a tiered cascade:

```
                           [ Incoming Media Batch ]
                                      │
                         [ Tier 1: SpeciesNet Ensemble ]
                                      │
                     ┌────────────────┴────────────────┐
              Animal Detected                        Blank
                     │                                 │
             [ Standard Flow ]                         ▼
                                         [ Tier 2: Heuristic Triage ]
                                         - In-burst blank (adjacent to animals)?
                                         - Motion ROI detected movement?
                                         - Edge AI detected animal?
                                         - Borderline confidence (0.15 - 0.49)?
                                                       │
                                        ┌──────────────┴──────────────┐
                                     Triaged Suspicious         Low-Risk Blank
                                            │                         │
                                            ▼                         ▼
                              [ Tier 3: Lightweight VLM ]      Confirmed Blank
                                (PaliGemma 2 / Qwen2.5-VL)
                                            │
                             ┌──────────────┴──────────────┐
                      VLM Confirms Animal          VLM Confirms Blank
                             │                             │
                             ▼                             ▼
                 Convert 'blank' → 'animal'         Retain 'blank'
                 Surface to Review Queue
```

---

## 4. The Role and Limitations of iNaturalist

There is a common misconception that iNaturalist can be used as a backend detector to identify which camera trap photos have animals.

### 4.1 Key Differences Between iNaturalist and Object Detectors
1. **Classifier vs. Detector:**
   * iNaturalist's Computer Vision system is an image classifier conditioned on taxonomy and geolocation (the Geomodel).
   * It assumes an organism is present in the photo. When given an empty forest frame or an undifferentiated rock, it will attempt to predict the most probable plant, fungus, or lichen species based on local occurrence data rather than classifying the frame as "empty".
2. **Community & Policy Constraints:**
   * iNaturalist is a biodiversity community platform, not an automated storage or QA clearinghouse for raw camera trap streams.
   * Uploading bulk camera trap images (especially blanks or poor-quality false triggers) degrades community review queues and violates iNaturalist's Terms of Service.

### 4.2 How Wildlife Watcher Interfaces with iNaturalist
In Wildlife Watcher's implementation (`documentation/development reports/inaturalist-integration.md`), iNaturalist integration is intentionally placed **downstream** of human or high-confidence review:

* **By-Catch Filter:** The publishing endpoint (`POST /api/inat/publish`) explicitly filters out `human`, `vehicle`, and `blank` observations.
* **Burst Consolidation:** Media captured within a short temporal gap ($\Delta t < 60\text{ s}$) are grouped into a single iNaturalist observation.
* **Two-Way Synchronization:** Once published, the platform polls iNaturalist to import Community ID taxonomy agreements back into Wildlife Watcher.

---

## 5. Recommended Actions & Implementation Plan

To enhance false-negative detection in Wildlife Watcher:

1. **Burst-Context Heuristic (Immediate):**
   * Implement an automated burst-consistency rule in `domain/pipeline.py`: If frame $N$ is marked `blank`, but frames $N-1$ and $N+1$ in the same burst contain animal detections, flag frame $N$ with `review_status = 'needs_review'` and an AL priority boost.
2. **Production Rollout of Motion ROI Fallback:**
   * Enable `FF_MOTION_ROI_FALLBACK_ENABLED` across production deployments to guarantee that missed animal silhouettes generate DINOv3 crops from motion differences.
3. **PaliGemma 2 / VLM Worker Prototype:**
   * Implement a background worker task (`AuditBlankTask`) using an open-weights grounded VLM (PaliGemma 2 3B or Qwen2.5-VL) that periodically audits a random 5% sample of blanks, plus 100% of blanks with high Active Learning uncertainty.
4. **Disagreement Telemetry on Model Evaluation:**
   * Surface Edge AI vs. Cloud AI detection discrepancies on the project dashboard to highlight camera sites with high false-negative rates due to camouflage or challenging local lighting.

---

## 6. Implementation (2026-09-26, extended 2026-09-29)

What is on this branch, how to run it, what it measured, what is still open. The design (evidence score, weights, bands, burst rules, consensus row and `media_evidence` contracts, prompt v2) lives in the [evidence-pipeline architecture report](../2026-09_evidence-pipeline-architecture/README.md), sections 5 to 9; nothing here restates it.

### 6.1 Decisions

| Date | Decision |
|---|---|
| 2026-09-26 | Gemini scores **every** frame, beside SpeciesNet, each model writing its own rows; nothing overwrites another model's row |
| 2026-09-26 | Access through a Google AI Studio key and the `google-genai` SDK (`GEMINI_API_KEY`, empty = disabled); no torch, ships in the lean API image |
| 2026-09-26 | Three token-saving variants, single frame / burst contact sheet / Batch API; **not** the motion-ROI crop (it removes the context the prompt asks about) |
| 2026-09-26 | Ground truth is labelled by hand (`label_presence.py`); SpeciesNet's verdicts cannot be the reference for a detector-miss study |
| 2026-09-26 | Jev AI dropped: text-only, would need another model to describe the frame first |
| 2026-09-29 | Gemini's own confidence is not a probability (1.0 on most answers) and is excluded from every decision; prompt v2 asks for structured evidence instead and the row's `confidence` is computed by us |
| 2026-09-29 | The final verdict is a separate consensus row from a weighted evidence score (weights v1, hand-set, threshold 0.50, suspicious band from 0.25) |
| 2026-09-29 | One burst grouper for every stage (`domain/burst_evidence.py::group_bursts`): firmware sequence tag first, else a 10 s timestamp gap (`BURST_GAP_SECONDS`; today's firmware spaces one trigger 3 to 5 s apart), never across a deployment or folder. `MOTION_ROI_BURST_GAP_SECONDS` is gone |
| 2026-09-29 | Fusion signals persist in the ww-backend table `media_evidence`; until it exists the writer probes once, logs and skips |
| 2026-10-09 | Production stays on prompt v1 (`PROMPT_VERSION`); v2 and v3 are selectable in the eval and through the run's `gemini_prompt_version`. v1's own confidence is still stored and still never a fusion input; fusion scores a v1 answer as a plain yes or no |
| 2026-10-09 | `Tommy_WW_Tests` leaves the labelled set (see the labelled set below) |
| 2026-10-09 | Prompt v3: animals only, a person is never an animal (`has_person` on its own), answers short enough to cost about what v1 does |

### 6.2 Files

| File | What |
|---|---|
| `backend/app/services/gemini_pricing.py` | The one price table and the image-token rules, each with source URL and date read; the per-prompt-version output-token estimate |
| `backend/app/services/gemini_presence.py` | Image preparation for the three variants, prompts v1 (the default), v2 and v3, the JSON schemas, parsing, `derived_confidence`, the `observation_comments` line (`format_verdict_comment` / `parse_verdict_comment`), token accounting, the three network functions tests mock |
| `backend/app/services/media_evidence.py` | `media_evidence` writer: one-shot existence probe, `signal_rows`, `write_signals`, `read_signal` |
| `backend/app/domain/burst_evidence.py` | The burst grouper, per-frame signal assembly, `evidence_score`, `consensus_type`, `band_of`, the audit line |
| `backend/app/domain/motion_roi.py` | `compute_motion_fractions`, the raw changed-pixel fraction per frame |
| `backend/app/domain/media_registry.py` | `group_bursts` is now a thin call into `burst_evidence.group_bursts` |
| `backend/app/domain/pipeline.py` | `GeminiPresenceStep` and `build_gemini_presence_observation`; `SpeciesNetStep` records `speciesnet_max_conf` before the threshold filter; `EvidenceFusionStep` and `build_consensus_observation` |
| `backend/app/schemas/pipeline.py` | `PipelineStepType.GEMINI_PRESENCE`, `EVIDENCE_FUSION`; `PipelineStepResult.input_tokens`, `output_tokens`, `cost_usd`, `counts` |
| `backend/app/jobs/definitions.py` | `build_pipeline_steps`: Gemini before SpeciesNet, fusion last |
| `backend/app/config.py` | `FF_GEMINI_PRESENCE_ENABLED`, `GEMINI_API_KEY`, `GEMINI_PRESENCE_MODEL`, `GEMINI_PRESENCE_VARIANT`, `FF_EVIDENCE_FUSION_ENABLED`, `EVIDENCE_FUSION_THRESHOLD`, `BURST_GAP_SECONDS` |
| `backend/scripts/label_presence.py` | Tkinter labeller, folders in, CSV out, resumable; optional strata columns `animal_size`, `visibility`, `conditions`, `distance` (architecture report section 10) |
| `backend/scripts/eval_presence.py` | Runs the variants over the labelled frames: dry-run cost, `--prompt-version`, `--only` (a subset by file list), `--max-calls`, `--cache-only`, `--dump-verdicts`, SpeciesNet dump and comparison, the stratified benchmark of architecture report section 10 |
| `backend/tests/test_gemini_presence*.py`, `test_label_presence.py`, `test_eval_presence.py`, `test_burst_evidence.py`, `test_evidence_fusion_step.py`, `test_media_evidence.py` | 118 tests, no network, SDK and Supabase client mocked; `test_six_frame_burst_worked_example_section_6_3` pins the architecture report's worked example |

Verified against the Gemini docs on 2026-09-26 (model ids, image tokens, bounding-box format, JSON mode, Batch API, SDK 2.25.0); the sources and rules are in the two service modules' docstrings.

### 6.3 How to run

**Label** (from `backend/`, any Python with Pillow; folders are read only):

```bash
python scripts/label_presence.py --out labels.csv --by charles \
    "C:/Users/ww/AE_photos_charles" "C:/Users/ww/ww-website/test-fixtures/camera-trap/sdcard" \
    "C:/Users/ww/person-test-images-run2/MEDIA" "C:/Users/ww/person-test-images" "C:/Users/ww/sample photos"
```

Keys: `a` animal, `e` empty, `u` unsure, `p` person, `b` back, `q` quit; after a verdict, the optional strata keys listed in the script's docstring and the window footer. `--list` prints the bursts without the window. An older CSV is widened in place to the current columns.

**Estimate the cost, no key:** `python scripts/eval_presence.py labels.csv --dry-run`

**Run the variants** (`GEMINI_API_KEY` in the root `.env`; `--cache` never pays twice, the cache key carries the prompt version):

```bash
python scripts/eval_presence.py labels.csv --variants single,contact_sheet,batch \
    --models gemini-3.1-flash-lite --prompt-version v2 --min-interval 4.2 \
    --cache eval_cache.jsonl --dump-verdicts verdicts.json --out results.md
```

**SpeciesNet on the same table** (needs torch, so dump once inside the dev Docker image):

```bash
python scripts/eval_presence.py labels.csv --dump-speciesnet speciesnet.json
python scripts/eval_presence.py labels.csv --speciesnet-results speciesnet.json --cache eval_cache.jsonl --out results.md
```

**The stratified benchmark from the caches**, no API call (`--cache-only`); the dump's Docker paths are re-rooted at the export folder:

```bash
python scripts/eval_presence.py labels-2026-09-26.csv --root <export folder> --variants single --prompt-version v1 \
    --cache eval_cache.jsonl --cache-only --speciesnet-results speciesnet.json --speciesnet-root /photos --out strata.md
```

**The pipeline**, set on the ARQ worker (the API only needs them when the pipeline runs in-process):

```
FF_ML_ENABLED=true
FF_PIPELINE_ENABLED=true
FF_GEMINI_PRESENCE_ENABLED=true
GEMINI_API_KEY=<AI Studio key>
GEMINI_PRESENCE_MODEL=gemini-3.1-flash-lite     # default
GEMINI_PRESENCE_VARIANT=single                   # single | contact_sheet | batch
FF_EVIDENCE_FUSION_ENABLED=true
EVIDENCE_FUSION_THRESHOLD=0.5                    # default
BURST_GAP_SECONDS=10.0                           # default; shared by motion ROI, contact sheet and fusion
```

Step results and the `gemini_presence_step_complete` / `speciesnet_step_complete` / `evidence_fusion_step_complete` log events carry tokens, USD, evidence rows written and, for fusion, the band counts. With `media_evidence` missing the log shows `media_evidence_table_missing` once, then `media_evidence_write_skipped` per write.

### 6.4 Prices

From https://ai.google.dev/gemini-api/docs/pricing, read 2026-09-26, USD per 1M tokens; Batch is 50% of standard. Cost per call = input tokens x input rate + (answer + thinking tokens) x output rate.

| Model | Input | Output | Batch input | Batch output | Note |
|---|---:|---:|---:|---:|---|
| `gemini-3.1-flash-lite` | 0.25 | 1.50 | 0.125 | 0.75 | default, cheapest open tier |
| `gemini-3.5-flash-lite` | 0.30 | 2.50 | 0.15 | 1.25 | "fastest, most cost-effective" |
| `gemini-3.8-flash` | 0.75 | 3.75 | 0.375 | 1.875 | promotional through 2026-12-31, then 1.50 / 7.50 |
| `gemini-3.5-flash` | 1.50 | 9.00 | 0.75 | 4.50 | |
| `gemini-2.5-flash-lite` | 0.10 | 0.40 | 0.05 | 0.20 | access limited to prior users |
| `gemini-2.5-flash` | 0.30 | 2.50 | 0.15 | 1.25 | access limited to prior users |

### 6.5 Results

**Smoke test, prompt v1, 2026-09-26.** 8 hand-picked frames, `gemini-3.1-flash-lite`, `MEDIA_RESOLUTION_LOW`, about $0.003:

| Variant | Calls | Prompt tokens per call | Output tokens per call | USD per frame | USD per 1,000 | Median latency |
|---|---:|---|---|---:|---:|---:|
| single (640x480) | 8 | 397 = 266 image + 131 text | 46 to 79 | $0.00019 | $0.19 | 2.2 s |
| contact_sheet (1 to 2 cells) | 6 | 479 to 483 = 266 to 270 image + 213 text | 70 to 94 per cell | $0.00021 | $0.21 | 2.1 s |

A Gemini 3 model bills the image as a fixed block (266 to 270 tokens at low), not by 768 px tiles, so downscaling is a no-op on WW500 frames and the sheet only saves with three or more frames per burst. The model reported confidence 1.0 on most answers.

**Labelled set, 2026-09-26, corrected 2026-09-30, reduced 2026-10-09.** 700 WW500 frames from five exports: three Colorado exports (600 frames, deployment `ad4ca41f`, June 2026, monochrome frames of a small rodent at close range, taken 17:35 to 20:19 local in ambient light: the file names are MDT, the EXIF times UTC, and no frame fired the IR flash, so the set has no real night frames, [#302](https://github.com/wildlifeai/ww-website/issues/302)) and two MEDIA exports (100). 577 animal, 41 empty, 82 person. `Tommy_WW_Tests` (262 frames) was removed because it is a bench grid with a plush toy and a hand, whose labels do not fit a wildlife-presence question, and its test photos carry no deployment ID (they are now skipped at upload, [#287](https://github.com/wildlifeai/ww-website/issues/287)). Only 41 empty frames remain, so every empty-removal figure below is thin. The person class holds the `MEDIA_VICTOR_090626` person-detection frames, first labelled animal; it is kept out of the wildlife metrics and reported on its own line. `20260616190039_01` was relabelled animal on review. `labels-2026-09-26.csv` beside this report, paths relative to the export folder (`eval_presence.py --root <folder>`).

**Interim evaluation, prompt v1, 2026-09-28**, free tier (15 requests per minute, 500 per day, no Batch API), recomputed 2026-09-30 against the corrected labels. The single variant reached 632 of the 700 frames: 600 Colorado and 32 MEDIA frames, so empty removal still rests on 39 frames. Raw verdicts in `eval-cache-single-2026-09-28.jsonl` (a record; keys hold the relative paths, so a live run keeps its own cache).

| Variant | Model | Frames | Recall (animal) | FN rate | Precision | Empty removed | Tokens/frame | USD/frame | USD/1,000 | Median latency | T3 (>=85% removed, <1.5% FN) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| single | gemini-3.1-flash-lite | 616 wildlife (577 animal, 39 empty) | 98.1% | 1.9% | 100% | 100% (39 of 39) | 473 | $0.00021 | $0.21 | 2.03 s | no (FN 1.9%) |

Person frames (16 answered): prompt v1 called 14 of them an animal.
| contact_sheet | gemini-3.1-flash-lite | 7 sheets | not enough data | | | | | | | | |
| batch | gemini-3.1-flash-lite | 0 | needs a billed tier | | | | | | | | |

The 11 misses, all one Colorado evening, a dark low-contrast rodent a few centimetres from the lens with no eye shine: `20260616174202_01`, `180016_01`, `180044_01`, `180650_01`, `181231_01`, `181256_01`, `190648_01`, `190653_01`, `193452_01`, `193459_01`, `194801_01` under `2026-07-17_ad4ca41f`.

**Smoke test, prompt v2 draft, 2026-09-29.** 20 live calls, `gemini-3.1-flash-lite`, single variant, 4.2 s apart, about $0.006, over the 11 misses, the false positive, 4 Colorado hits and 4 NZ frames. The prompt was the first v2 draft (same near-lens cue and fields, the earlier vocabulary: visibility none/partial/full, nine location cells, free-text evidence); the section 9 vocabulary landed after the budget was spent, so the run should be repeated. Cache `eval-cache-single-v2-smoke.jsonl` in the session scratchpad, keyed `prompt: v2`.

| | Value |
|---|---|
| Input tokens per call | 494 = 266 image + 228 prompt (v1 prompt: 131) |
| Output tokens per call | 93 to 151 (93 to 108 empty, 121 to 151 animal with an evidence list) |
| USD per frame / per 1,000 | $0.00030 / $0.30 (v1: $0.21) |
| Median latency | 1.97 s |

| Frame | v1 | v2 draft | Visibility, size | Evidence given |
|---|---|---|---|---|
| `20260616174202_01` | blank | **animal** | partial, small | tail on the right, part of a small body |
| `20260616190648_01` | blank | **animal** | partial, small | rodent shape bottom right, snout and ear |
| `20260616193452_01` | blank | **animal** | partial, small | tail on the left edge |

Of the 11 misses, 5 recovered (`174202`, `181256`, `190648`, `190653`, `193452`) and 6 still blank (`180016`, `180044`, `180650`, `181231`, `193459`, `194801`: dry grass, leaf litter, a feather). The 4 Colorado hits stayed animal, the empty Colorado frame stayed blank, `190039_01` came back animal again ("blurred tail shape, fur texture"), now its label. The two person frames (`A27ACD90`, `A27ACDE0`) came back empty with the person named in the description, where v1 called most person frames an animal. The near-lens cue recovers about half of the hard misses at 1.4x the v1 cost; the rest are the burst cases the fusion step exists for.

**Benchmark on the reduced set, 2026-10-09.** No new calls: the v1 and v2 caches (session scratchpad) and the SpeciesNet dump rescored on the 700 frames, each run on the frames it answered (v1 did not reach 66 person and 2 empty frames). Box rules: frame area 0.90, frame confidence 0.50, vehicles dropped.

| Run | Answered (animal + empty) | Recall | FN | Precision | Empty removed | Person called animal | Tokens/frame | USD/1,000 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Gemini v1 | 577 + 39 | 98.1% | 11 | 100.0% | 100.0% (39 of 39) | 14 of 16 | 473 | $0.21 |
| Gemini v2 | 577 + 41 | 98.8% | 7 | 99.8% | 97.6% (40 of 41) | 68 of 82 | 732 | $0.36 |
| SpeciesNet | 577 + 41 | 94.6% | 31 | 99.6% | 95.1% (39 of 41) | 0 of 82 | n/a | local |
| SpeciesNet + box rules | 577 + 41 | 94.5% | 32 | 99.6% | 95.1% (39 of 41) | 0 of 82 | n/a | local |

On the 632 frames v1 and v2 both answered, v2 is 98.8% recall against v1's 98.1% and removes 38 of 39 empty frames against 39. v2 called 68 of 82 person frames an animal while its own description named the human, so production stays on v1. With 41 empty frames the empty-removal column is a few frames either way.

**Stratified benchmark, 2026-10-10** ([#163](https://github.com/wildlifeai/ww-website/issues/163), strata in architecture report section 10). No new calls: the v1 cache and the SpeciesNet dump on the 700 frames, answered frames only, recall with its 95% Wilson interval for every stratum with 30 or more animal frames. Size and border come from one box per animal frame (SpeciesNet's, else Gemini's), so each stratum splits both runs the same way; the 6 frames neither model boxed have no size or border.

| Stratum | Animal frames | Gemini v1 recall | FN | SpeciesNet recall | FN |
|---|---:|---:|---:|---:|---:|
| day (whole set) | 577 | 98.1% (96.6 to 98.9%) | 11 | 94.6% (92.5 to 96.2%) | 31 |
| burst of 3 or more | 568 | 98.1% (96.6 to 98.9%) | 11 | 94.5% (92.4 to 96.1%) | 31 |
| export `colorado_..._1` | 523 | 98.3% (96.8 to 99.1%) | 9 | 94.8% (92.6 to 96.4%) | 27 |
| size large (box over 30%) | 175 | 100.0% (97.9 to 100.0%) | 0 | 96.0% (92.0 to 98.0%) | 7 |
| size medium (10 to 30%) | 332 | 98.8% (96.9 to 99.5%) | 4 | 97.6% (95.3 to 98.8%) | 8 |
| size small (2 to 10%) | 64 | 98.4% (91.7 to 99.7%) | 1 | 84.4% (73.6 to 91.3%) | 10 |
| on the border (box within 2% of an edge) | 519 | 99.0% (97.8 to 99.6%) | 5 | 95.8% (93.7 to 97.2%) | 22 |
| interior | 52 | 100.0% (93.1 to 100.0%) | 0 | 94.2% (84.4 to 98.0%) | 3 |

Too few to report: night IR (2 frames, both empty: no animal frame is night, as #302 found), bursts of 1 or 2 (7 and 2 animal frames), the two 30-frame Colorado exports (27 animal frames each), every deployment but `ad4ca41f` (which holds all 577 animal frames, so the deployment stratum is the whole set), tiny animals (none), and every human stratum (no frame carries one yet). The one finding with separated intervals is SpeciesNet on small animals: 84.4% against 97.6% on medium ones, where Gemini v1 holds 98.4%. Everything else is one Colorado evening, so the strata still need labelled night frames and other deployments before they say anything about them.

**Prompt v3, partial, 2026-10-09.** 36 of a planned 230-frame subset (every person frame, the 7 Colorado frames v2 missed, every empty frame, 100 animal frames drawn with seed 20261009) before the free tier's 500 requests a day ran out; all 36 are Colorado frames (32 animal, 4 empty). On those frames v1, v2 and v3 agree: the same 4 misses (`180016_01`, `180044_01`, `180650_01`, `181231_01`), no false positive. v3 costs 523 tokens per frame (454 in, 48 to 76 out) and $0.22 per 1,000, against 469 and $0.21 for v1 and 711 and $0.35 for v2 on the same frames. The person frames, the point of v3, are not scored yet.

T3 target, for reference: at least 85% of empty frames removed at under 1.5% false negatives; a frame the model failed to answer is kept, so it counts against the filter, never as a lost animal.

### 6.6 Open items

- Finish the v3 subset run (`--prompt-version v3 --only`, about 194 calls, one free-tier day), person frames first; v3 replaces v1 only if it keeps v1's recall and stops calling people animals.
- Label more empty frames from real deployments: 41 remain, too few for an empty-removal figure. The batch variant still needs a billed tier.
- The strata need night IR frames, other deployments and the human strata keys (`visibility`, `distance`, `conditions`) on the hard frames; today every reportable stratum is the one Colorado evening.
- Run `--dump-speciesnet` inside the dev Docker image over the same CSV so the SpeciesNet row exists and the fusion can be evaluated offline; then fit the weights.
- ww-backend: the `media_evidence` table ([ww-backend#208](https://github.com/wildlifeai/ww-backend/issues/208)); firmware: the `trigger_id` / `frame_index` EXIF tag ([Seeed#242](https://github.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/issues/242)). Until the table exists `near_threshold` is absent for every frame and the signals live only in the log and the consensus row's comment.
- Done: [#161](https://github.com/wildlifeai/ww-website/issues/161) (#178), both paths reflect the edge model before `run_pipeline`, so `edge_presence` is present at fusion time; [#162](https://github.com/wildlifeai/ww-website/issues/162) (#179), per-crop classification writes its own row.
- `annotation_runs.chk_annotation_run_provenance` needs a `model_id`; a run whose only step is Gemini has none, so the provenance insert logs `annotation_run_record_failed` (the observations are still written). Seed a cloud-model row in ww-backend ([ww-backend#209](https://github.com/wildlifeai/ww-backend/issues/209)).
- The observed Gemini 3 image tokens (266 to 270 at low) sit under the documented 280; the price table keeps 280 as the conservative estimate.
