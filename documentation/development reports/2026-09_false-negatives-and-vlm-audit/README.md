# Detector False Negatives, Wildlife Brain Fallbacks, and VLM Auditing

> **Status:** 📋 Proposal / Analysis · 2026-09-09 · Web Platform & AI Pipeline

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
