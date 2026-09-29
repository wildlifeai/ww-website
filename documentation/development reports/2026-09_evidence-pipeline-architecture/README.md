# Evidence pipeline architecture: recall, evidence preservation, cost

> **Status:** 📋 Proposal, 2026-09-29; decisions updated 2026-09-30. Items 1 to 3 of section 4
> are in progress on branch `docs/2026-09-detector-false-negatives-and-vlm-audit`; items 4 to 10
> are issues to file (section 17). This report owns the evidence design: layers, score, weights,
> bands, consensus row, `media_evidence`, prompt v2, burst rules. The detector false-negative
> analysis is sections 1 to 5 of
> [2026-09_false-negatives-and-vlm-audit](../2026-09_false-negatives-and-vlm-audit/README.md);
> the Gemini experiment (how to run, results, prompt versions measured) is section 6 of the same
> report (lands with `feat/gemini-presence-evidence-fusion`). Google Cloud rates
> are in [2026-09_gcp-pilot-and-migration](../2026-09_gcp-pilot-and-migration/README.md) (lands
> with `docs/gcp-pilot-and-migration`). Schema items are proposals to ww-backend, never
> migrations here.

## Contents

1. [Decisions](#1-decisions)
2. [The six layers](#2-the-six-layers)
3. [The fourteen points](#3-the-fourteen-points)
4. [This iteration](#4-this-iteration)
5. [Burst grouper rules](#5-burst-grouper-rules)
6. [Evidence score v1](#6-evidence-score-v1)
7. [Consensus row contract](#7-consensus-row-contract)
8. [`media_evidence` contract](#8-media_evidence-contract)
9. [Gemini prompt v2: structured evidence](#9-gemini-prompt-v2-structured-evidence)
10. [Stratified benchmark](#10-stratified-benchmark)
11. [Cost model per 100,000 frames](#11-cost-model-per-100000-frames)
12. [Cost per recovered false negative](#12-cost-per-recovered-false-negative)
13. [Escalation budget](#13-escalation-budget)
14. [Disagreement matrix](#14-disagreement-matrix)
15. [False-negative dataset](#15-false-negative-dataset)
16. [Learned cascade](#16-learned-cascade)
17. [Not yet, and open items](#17-not-yet-and-open-items)

Code marked * exists only on branch `docs/2026-09-detector-false-negatives-and-vlm-audit`;
everything else is on dev. Paths are under `backend/app/` unless they start with `scripts/` or
`tests/` (under `backend/`).

## 1. Decisions

| Topic | Decision, and why |
|---|---|
| Unit of evidence | The trigger burst, not the frame, because a blank between two animal frames is evidence, not an independent verdict |
| Burst identity | Firmware sequence tag first, else EXIF timestamp gap `BURST_GAP_SECONDS`, default **10 s**, never across a deployment or folder, because today's firmware spaces burst frames 3 to 5 s apart (Colorado frames 194749, 194752, 194756) and the tag makes the gap irrelevant for new firmware |
| Burst grouper | One: `domain/burst_evidence.py::group_bursts`*, with `domain/media_registry.py::group_bursts` delegating to it, because three groupers with different gaps disagreed (section 3, point 2) |
| Final presence | One consensus observation per media (`source_type='consensus'`, `classified_by='evidence_fusion_v1'`), per-model rows untouched, so the table is the audit trail |
| Fusion rule v1 | Weighted sum, hand-set weights, threshold 0.50, suspicious band from 0.25 (section 6), because nothing is labelled enough to fit yet; the disagreement matrix replaces the weights |
| Gemini confidence | Not requested and never a fusion input, because v1 returned 1.0 on most answers |
| Prompt | v2 structured evidence (section 9), because its fields feed fusion and the strata |
| Production wiring | Gemini on every frame beside SpeciesNet; auditor-on-suspicious-blanks (section 13) only once the score has human-reviewed outcomes |
| Signal storage | New ww-backend table `media_evidence` (section 8), because `observations` has no jsonb column and `media.exif_metadata` belongs to the EXIF parser |
| Benchmark tier | Free Gemini tier, because no billing account exists yet; Batch cost is estimated from the published 50% discount |
| Vote or replacement | Neither until per-model, combined and suspicious-only recall are measured on human labels across strata |

## 2. The six layers

```text
frames from the SD card, one burst per trigger
  L1 capture / quality    burst grouping; image-quality class
  L2 fast presence        SpeciesNet (cloud); Edge AI scores from EXIF (camera)
  L3 cheap evidence       motion fraction; burst neighbour; near-threshold confidence;
                          border proximity; DINO anomaly (no model calls, no API cost)
  L4 FN recovery          evidence fusion -> consensus row; Gemini structured evidence;
                          localisation recovery
  L5 species              BioCLIP; SpeciesNet classifier; iNaturalist; human
  L6 learning / eval      human truth; FN dataset; stratified benchmark; disagreement
                          matrix; learned cascade -> weights back into L4
```

| Layer | Exists today | Rule |
|---|---|---|
| L1 | `domain/media_registry.py::group_bursts` (10 s, motion ROI only); `domain/clustering.py::laplacian_variance_sharpness`; `domain/exif.py::parse_maker_note_fields` | Poor quality lowers trust in a blank, never asserts an animal |
| L2 | `domain/pipeline.py::build_speciesnet_observations`; `domain/edge_reflection.py::build_edge_observations` | Each source writes its own row; neither is final |
| L3 | `domain/motion_roi.py::compute_motion_roi_per_frame`; `domain/active_learning.py::combine_al_score` | No model calls |
| L4 | `domain/pipeline.py::GeminiPresenceStep`*, `EvidenceFusionStep`* | Only the consensus row carries the verdict |
| L5 | `domain/pipeline.py::BioCLIPStep` | Species classifiers never decide presence |
| L6 | `domain/active_learning.py::get_review_queue`, `qa_report`; `scripts/label_presence.py`*, `scripts/eval_presence.py`* | Only a human closes a case |

## 3. The fourteen points

| # | Point | Layer | Exists today | This iteration / issue |
|---|---|---|---|---|
| 1 | Explicit false-negative evidence score | L4 | `domain/active_learning.py::combine_al_score` (review order; AI vs human, not model vs model) | Item 2; weights from issue 7, then 10 |
| 2 | Burst as the unit | L1, L3 | `domain/media_registry.py::group_bursts`, `scripts/label_presence.py::group_bursts`*, `domain/events.py::_cluster_temporal` (30 min ecological event, unchanged) | Item 2; P7 |
| 3 | Never overwrite a model's verdict | L4 | `domain/pipeline.py::delete_superseded_ai_observations` honours it; `BioCLIPStep._refine_crops_per_detection` breaks it | Item 3; P5 |
| 4 | Presence, localise, species, confidence, human as separate stages | L2, L4 to L6 | `build_speciesnet_observations` decides blank from the detector only; `build_edge_observations` records nothing below threshold | Item 2 records `edge_presence = 0`; P6 |
| 5 | Localisation recovery | L4 | `domain/media_registry.py::generate_motion_roi_crops` (`FF_MOTION_ROI_FALLBACK_ENABLED`, off) | Issue 6 |
| 6 | Image-quality class before presence | L1 | no | Issue 5 |
| 7 | VLM confidence is not a probability | L4 | `services/gemini_presence.py`* prompt v1 | Item 1 (prompt v2) |
| 8 | Close the human-review loop | L6 | `domain/active_learning.py::get_review_queue`, `qa_report` | Issue 8 |
| 9 | Stratified benchmark | L6 | `scripts/eval_presence.py::compute_metrics`* (whole set) | Item 1; issue 4 |
| 10 | System cost budget | L6 | `services/gemini_pricing.py`*, `PipelineStepResult.cost_usd`* | Section 11; P8 |
| 11 | Escalation budget per deployment | L4 | no | Issue 9 |
| 12 | Model disagreement matrix | L6 | `domain/active_learning.py::compute_qa_metrics` (one AI label vs one human label) | Issue 7 |
| 13 | Learned false-negative-risk classifier | L6, L4 | no | Issue 10 |
| 14 | No vote, no Gemini replacing SpeciesNet yet | all | n/a | Section 17 |

## 4. This iteration

| # | Item | Where |
|---|---|---|
| 1 | Finish the benchmark: remaining single frames, contact sheets, v2 re-run of the cached v1 verdicts, SpeciesNet dump in the dev image, per-stratum recall | September report §6.5 (how to run), §6.7 (what is left) |
| 2 | Burst evidence scoring: grouper, six v1 signals, score, `media_evidence` writer | `domain/burst_evidence.py`*, `domain/motion_roi.py::compute_motion_fractions`*, `services/media_evidence.py`* |
| 3 | Per-model rows preserved plus one consensus row | `domain/pipeline.py::EvidenceFusionStep`* |

Pipeline order (`jobs/definitions.py::build_pipeline_steps`); fusion is last because it reads
every other step's rows:

```text
MEDIA_PREP -> GEMINI_PRESENCE -> SPECIESNET -> ANIMAL_CROP -> BIOCLIP -> EVIDENCE_FUSION
```

Settings (set on the ARQ worker, not only the API):

| Variable | Default | Effect |
|---|---|---|
| `FF_GEMINI_PRESENCE_ENABLED` | false | Gemini step before SpeciesNet |
| `FF_EVIDENCE_FUSION_ENABLED` | false | Fusion step, consensus rows, `media_evidence` writes |
| `EVIDENCE_FUSION_THRESHOLD` | 0.5 | Consensus animal at or above |
| `BURST_GAP_SECONDS` | 10 | Gap when the sequence tag is absent; replaces `MOTION_ROI_BURST_GAP_SECONDS` (P7) |

## 5. Burst grouper rules

`domain/burst_evidence.py::group_bursts(media_rows, gap_seconds=BURST_GAP_SECONDS)`*, pure.

| Rule | Contract |
|---|---|
| Scope | One deployment and one folder (directory part of `file_path`); a burst never spans either |
| Tag first | When `exif_metadata` carries `trigger_id`: one burst per `(deployment, folder, trigger_id)`, ordered by `frame_index`, timestamps ignored; `burst_len` is the frames present, which may be fewer than `frame_count` |
| Gap otherwise | Sort by `media.timestamp`, then file name; consecutive frames at most `BURST_GAP_SECONDS` apart (default 10 s) are one burst |
| Mixed | Tagged and untagged frames never share a burst |
| No timestamp | Singleton. BMP frames in file-based tools use the 8.3 hex filename clock `(seconds << 4) + sub_second` (`scripts/label_presence.py::hex_name_seconds`*) |
| `burst_id` | `<deployment_id>:<trigger_id>`, else `<deployment_id>:<media_id of the first frame>`, stored as `value_text` |
| `burst_index` | 0-based; from the tag, `frame_index - 1` (the tag's index is 1-based) |
| Singleton | `burst_len = 1`, `burst_animal_count = 0`, `neighbour_animal` absent (not 0), so "no neighbours" differs from "neighbours were blank" |
| Representatives for a VLM call | Top 1 to 3 frames by `motion_frac`, ties broken by `laplacian_variance_sharpness`; a singleton is its own |
| Events | `domain/events.py::_cluster_temporal` keeps its 30 min per-taxon gap; a burst always lies inside one event |

## 6. Evidence score v1

### 6.1 Signals

All values in [0, 1]. Absent means not computable for the frame: no row, never 0.

| Signal | Definition | Absent when |
|---|---|---|
| `speciesnet_presence` | 1 if SpeciesNet wrote a non-blank media-level row (animal, human, vehicle, unknown), else 0 | SpeciesNet did not run |
| `speciesnet_max_conf` | Maximum detector confidence over **all** detections, recorded by `SpeciesNetStep` before the threshold filter; 0 when none | SpeciesNet did not run |
| `near_threshold` | `min(1, speciesnet_max_conf / confidence_threshold)`, threshold default 0.2 | `speciesnet_max_conf` absent |
| `gemini_presence` | 1.0 for `clear` or `partial`, 0.8 for `obscured`, 1.0 for a v1 answer, 0 when `has_animal` is false | No Gemini row |
| `motion_frac` | Changed-pixel fraction of the 320x240 grayscale mask against the burst's first frame (the first frame against the second), diff threshold 15 | Singleton, or fewer than two frames of the burst resolved |
| `motion` | `min(1, motion_frac / 0.02)`; `motion_frac > 0.6` (camera shift, light change) gives 0 and a `camera_shift` flag | `motion_frac` absent |
| `neighbour_animal` | 1 if any other frame in the burst has `speciesnet_presence = 1` or Gemini `has_animal`, else 0 | Singleton |
| `edge_presence` | 1 if an `ai_origin='edge'` row exists; 0 if EXIF `user_comment_fields` carry NN scores and none cleared | No NN scores in EXIF |
| `edge_score` | Highest target-label score / 100 | As above |

### 6.2 Weights, threshold, bands

```python
# domain/burst_evidence.py. Hand-set guesses, replaced by the disagreement matrix.
WEIGHTS_V1 = {
    "speciesnet_presence": 0.50,
    "gemini_presence":     0.50,
    "neighbour_animal":    0.25,
    "motion":              0.15,
    "edge_presence":       0.15,
    "near_threshold":      0.10,
}
THRESHOLD_V1 = 0.50      # score >= threshold -> consensus 'animal'
SUSPICIOUS_V1 = 0.25     # [0.25, 0.50) -> blank, suspicious
WEIGHTS_VERSION = "v1"
# score = round(clamp(sum(weight * value over PRESENT signals), 0, 1), 4); absent adds nothing
```

| Signal | Weight | Why (v1 guess) |
|---|---:|---|
| `speciesnet_presence` | 0.50 | A kept detection reaches the threshold alone |
| `gemini_presence` | 0.50 | Symmetric with SpeciesNet; an `obscured` positive (0.40) needs one cheap signal |
| `neighbour_animal` | 0.25 | Alone it reaches the suspicious band and never flips a frame |
| `motion` | 0.15 | Neighbour plus motion (0.40) stays blank; plus a near-threshold box (0.50) flips |
| `edge_presence` | 0.15 | Treated like motion until its per-version precision is measured |
| `near_threshold` | 0.10 | Weak but real; a kept box already counts through `speciesnet_presence` |

Recall-biased: either fast model carries a frame alone, cheap evidence only adds, nothing
subtracts. The score is an ordering, not a probability.

| Band | Consensus | Handling |
|---|---|---|
| `score >= 0.50` | `animal` (or SpeciesNet's non-blank type) | Crop, species, event |
| `0.25 <= score < 0.50` | `blank`, suspicious | Top of the review queue; escalation candidates |
| `score < 0.25` | `blank`, confirmed | Source of the random low-risk sample (section 13) |

### 6.3 Worked example: a six-frame burst (test fixture)

Fixture for `tests/test_burst_evidence.py::test_six_frame_burst_worked_example_section_6_3`*.
SpeciesNet threshold 0.2, no edge model (`edge_presence` absent), prompt v2.

| Frame | SpeciesNet | `max_conf` | `near` | Gemini | `motion_frac` | `motion` | neighbour | Score | Consensus |
|---:|---|---:|---:|---|---:|---:|---:|---:|---|
| 1 | blank | 0.03 | 0.15 | no | 0.000 | 0.00 | 1 | 0.2650 | blank, suspicious |
| 2 | blank | 0.14 | 0.70 | yes, partial | 0.012 | 0.60 | 1 | 0.9100 | **animal** (recovered) |
| 3 | animal 0.71 | 0.71 | 1.00 | yes, clear | 0.031 | 1.00 | 1 | 1.0000 | animal |
| 4 | animal 0.66 | 0.66 | 1.00 | yes, clear | 0.028 | 1.00 | 1 | 1.0000 | animal |
| 5 | blank | 0.17 | 0.85 | no | 0.009 | 0.45 | 1 | 0.4025 | blank, suspicious |
| 6 | blank | 0.02 | 0.10 | no | 0.001 | 0.05 | 1 | 0.2675 | blank, suspicious |

## 7. Consensus row contract

One row per media, written through
`delete_superseded_ai_observations(svc, media_ids, "evidence_fusion_v1")`. Columns per
ww-backend `supabase/schemas/public/tables/35_observations.sql`:

| Column | Value | Note |
|---|---|---|
| `id` | new uuid | |
| `deployment_id`, `media_id` | the media's | composite FK |
| `observation_event_id` | NULL | `events.py` links it later |
| `observation_level` | `'media'` | |
| `observation_type` | SpeciesNet's kept non-blank type (`human`, `vehicle`, `unknown`, `animal`), else `'animal'` if `score >= 0.50`, else `'blank'` | Gemini answers only animal or blank |
| `taxon_id`, `scientific_name`, `vernacular_name` | NULL | Presence only in v1; species stays on the per-model rows (precedence human > cloud > edge, `AI-ARCHITECTURE.md`) |
| `bbox_x/y/w/h` | NULL | No box fusion in v1 |
| `classifier_category`, `crop_url`, `count`, `life_stage`, `sex`, `behavior`, `individual_id` | NULL | |
| `source_type` | `'consensus'` | |
| `ai_origin` | NULL | the CHECK allows it for consensus rows |
| `source_model_id` | NULL | no `ai_models` row for the fusion |
| `source_model_version` | `'evidence_fusion_v1'` | the replace key |
| `annotator_id`, `reviewer_id` | NULL | |
| `review_status` | `'ai_reviewed'` | **not** `'consensus_approved'`, which `active_learning.py::_HUMAN_REVIEWED` counts as human truth |
| `confidence` | the evidence score | an ordering, not a probability |
| `classification_method` | `'machine'` | |
| `classified_by` | `'evidence_fusion_v1'` | |
| `classification_timestamp` | run time, UTC ISO | |
| `classification_probability`, `embedding_run_id`, `cluster_id`, `observation_tags`, `deleted_at` | NULL | |
| `observation_comments` | `evidence_fusion_v1 score=0.91 threshold=0.50 speciesnet=0 gemini=1.0 neighbour=1 motion=0.60 edge=absent near=0.70` | audit line, under 500 characters |

Uniqueness is enforced by the writer. If ww-backend wants it in the schema:
`unique (media_id) where source_type = 'consensus' and deleted_at is null` (partial index, not
part of the `media_evidence` issue).

## 8. `media_evidence` contract

Owned by ww-backend; the ready-to-file issue with full DDL, RLS and pgTAP is
[`issue-ww-backend-media-evidence.md`](issue-ww-backend-media-evidence.md).

```sql
media_evidence(
  id uuid pk,
  media_id uuid not null fk media,
  deployment_id uuid not null,
  signal text not null,
  value float4,
  value_text text,
  source text not null,
  source_version text,
  computed_at timestamptz not null default now(),
  run_id uuid
)
unique (media_id, signal, source, source_version)
RLS mirroring observations
```

| Rule | Contract |
|---|---|
| Re-run | Same `source_version` upserts; a new one adds rows and keeps the old |
| Values | `value` for numbers, `value_text` for identities and labels; at least one set |
| `run_id` | Not a foreign key, because the `annotation_runs` row is best-effort |
| Access | Website writes with the service role; `authenticated` reads under the `observations` deployment-to-project scope |
| Signal names | Enumerated in `domain/burst_evidence.py`*, not a CHECK, so a new signal is a code change, not a migration |
| Table missing | `services/media_evidence.py::write_signals`* logs `media_evidence_write_skipped` and returns 0; the consensus row and its audit line are still written |

| Signal | `value` | `value_text` | `source` (`source_version`) |
|---|---|---|---|
| `speciesnet_presence` | 0 or 1 | | `speciesnet` (`speciesnet-v4.0.1a`) |
| `speciesnet_max_conf` | 0 to 1 | | `speciesnet` |
| `gemini_presence` | 0 or 1 (`has_animal`) | | `gemini` (`<model id>:<prompt version>`, so a new prompt adds rows) |
| `gemini_visibility` | clear 1.0, partial 0.66, obscured 0.33, none 0 | label | `gemini` |
| `gemini_size` | tiny 0.25, small 0.5, medium 0.75, large 1.0, none 0 | label | `gemini` |
| `motion_frac` | 0 to 1 | | `motion` (`motion_v1`) |
| `edge_presence` | 0 or 1 | | `edge` (`evidence_fusion_v1`) |
| `edge_score` | 0 to 1 | label | `edge` |
| `burst_id` | | `<deployment_id>:<trigger or first media id>` | `bursts` (`bursts_v1`) |
| `burst_index` | 0-based | | `bursts` |
| `burst_len` | integer | | `bursts` |
| `burst_animal_count` | integer, other frames with a model positive | | `bursts` |
| `neighbour_animal` | 0 or 1 | | `bursts` |
| `evidence_score` | 0 to 1 | | `fusion` (`evidence_fusion_v1`) |
| `evidence_threshold` | 0.50 | | `fusion` |
| `evidence_weights_version` | | `v1` | `fusion` |

## 9. Gemini prompt v2: structured evidence

Replaces the v1 prompt and schema in `services/gemini_presence.py`*; the contact sheet asks
the same fields per cell. The JSON schema enumerates every string, so a stray label is a parse
error.

```text
Examine this camera trap image. It may have been automatically flagged as empty. Inspect
foliage, shadows, ground textures and the frame borders for wildlife evidence: reflective
eye shine, a body outline, fur or feather texture, a partial limb, tail, ear or snout,
or a motion-blur trail. Small rodents very close to the lens on night IR frames can appear
as a dark low-contrast shape with no eye shine and no clear outline. Insects and spiders
on the lens do not count. Return JSON:
  has_animal: bool
  animal_visibility: "clear" | "partial" | "obscured" | "none"
  animal_size: "tiny" | "small" | "medium" | "large" | "none"   (share of the frame:
      under 2%, 2 to 10%, 10 to 30%, over 30%)
  animal_location: "centre" | "edge" | "corner" | "none"
  visual_conditions: list of "night_ir", "low_light", "motion_blur", "rain", "fog",
      "lens_obstruction", "overexposed", "underexposed", "vegetation", "none"
  evidence: list of "eye_shine", "body_outline", "fur_texture", "limb", "tail", "head",
      "motion_trail", "shadow", "none"
  description: one sentence
  bbox: [ymin, xmin, ymax, xmax] normalised to 0-1000 when an animal is visible, else null
```

No `confidence` is requested. The Gemini row's `observations.confidence` is computed, displayed
and **not** a fusion input (fusion reads `has_animal` and the visibility label only):

```text
gemini_confidence = clamp(visibility_weight[animal_visibility]
                          + 0.05 x min(3, len(evidence) excluding "none"), 0, 1)
visibility_weight = {clear: 0.85, partial: 0.70, obscured: 0.50, none: 0.0}
```

`animal_size` and `animal_location` feed the automatic strata of section 10.

## 10. Stratified benchmark

Recall per stratum with `n` and a 95% Wilson interval; no stratum reported as a number below
30 animal frames.

| Stratum | Derivation | How |
|---|---|---|
| day / night IR | automatic | grayscale test (R = G = B on a pixel sample), or EXIF `flash_fired` (tag 0x9209 bit 0, or MakerNote field 8: visible 1, IR 2), `camera_variant = HM0360` |
| deployment | automatic | `media.deployment_id`; folder in file-based tools |
| burst length | automatic | `burst_len` |
| small / large | automatic with a box | box area over frame area, SpeciesNet or Gemini box: small under 2%, large over 20%; else `gemini_size`; else human |
| border | automatic with a box | box within 2% of a frame edge; else `animal_location` in (`edge`, `corner`); else human |
| motion blur | automatic proxy, human confirms | `laplacian_variance_sharpness` below a per-camera cut set from the labelled frames |
| close / distant | human | `distance` column |
| camouflaged, partial | human | `visibility` column |
| rain, vegetation, fog, obstruction | human | `conditions` column; the quality stage (issue 5) automates these later |
| genuine blank | label | `has_animal = 0` |
| human, vehicle | human | `subject` column |

Columns appended to `scripts/label_presence.py`'s CSV, all optional, old CSVs still read:

| Column | Values | Status |
|---|---|---|
| `animal_size` | `tiny`, `small`, `medium`, `large` | On the vlm branch (September report §6.8.6) |
| `visibility` | `clear`, `partial`, `obscured`; issue 4 adds `camouflaged`, `border` | On the vlm branch, extended by issue 4 |
| `conditions` | semicolon list from prompt v2's `visual_conditions` | On the vlm branch |
| `subject` | `animal`, `human`, `vehicle`, `empty`, `unsure` | Issue 4 |
| `distance` | `close`, `mid`, `far`, `na` | Issue 4 |
| `taxon` | free text | Issue 4 |

`scripts/eval_presence.py`* prints recall per automatic stratum today; issue 4 adds the human
strata, `n`, the interval and the 30-frame floor, with a human column overriding the automatic
one for the same stratum.

## 11. Cost model per 100,000 frames

USD. Unknown stays unknown.

| Stage | Layer | Per 100,000 frames | Status | Source |
|---|---|---|---|---|
| Gemini presence, `single`, online, prompt v1 | L4 | **$21.00** ($0.21 per 1,000) | measured 2026-09-28, 600 frames | September report §6.6 |
| Gemini presence, prompt v2 | L4 | see source | measured on a v2 draft, 2026-09-29 | September report §6.8.7 |
| Gemini presence, Batch API | L4 | **$10.50** | estimated, published 50% discount | September report §6.3 |
| Gemini contact sheet | L4 | unknown (7 sheets scored) | unknown | September report §6.6 |
| SpeciesNet, BioCLIP, DINOv3 on the Azure T4 | L2, L3, L5 | unknown: 1 to 2 s per image, T4 per-second rate not recorded | unknown | [cloud-infrastructure.md](../../resources/cloud-infrastructure.md) |
| Same on the Cloud Run L4 job | L2, L3, L5 | **$35 to $70** (1 to 2 s per image at the L4 job rate), plus cold starts per execution | estimated; L4 speed unverified | [migration report §6.2](../2026-09_gcp-pilot-and-migration/README.md#62-the-worker-cost-per-photo) |
| Media resolve from Drive | L1 | unknown | unknown | |
| Burst grouping, motion, near-threshold | L3 | negligible, not measured (NumPy at 320x240, no extra download) | estimated | `domain/motion_roi.py` |
| Edge presence | L2 | $0 (parsed from EXIF at upload) | fact | `domain/exif.py` |
| Fusion rows | L4 | 100,000 consensus rows + about 1.6 million `media_evidence` rows, about 150 MB | estimated | 16 signals x 100 bytes |
| Human review | L6 | `100,000 x share_reviewed x t_review / 60 x rate` | unknown | |

## 12. Cost per recovered false negative

```text
recovered_FN(S) = |{ frames : SpeciesNet blank,
                              consensus animal because of S,
                              human confirms animal }|

cost_per_recovered_FN(S) = total_cost(S) / recovered_FN(S)

human_cost_per_FN = (blank_frames x t_review / 60 x rate) / FN_among_blanks
```

`total_cost(S)` from `PipelineStepResult.cost_usd` (Gemini), the GPU line (models), review time
(humans). A stage stays while `cost_per_recovered_FN(S) < human_cost_per_FN`, reported per
deployment and per stratum; unseen misses are estimated from the random sample of section 13.

## 13. Escalation budget

Target wiring once the score is calibrated; not production now.

| Rule | Contract |
|---|---|
| Budget | `B` frames per deployment per upload batch, from USD at the measured per-frame price; project setting with a default |
| Allocation | Batch blanks ranked by `evidence_score` descending, top `B` sent; at most 3 calls per burst (its representative frames) |
| Random sample | 5% of `B` drawn uniformly from `score < 0.25`; the miss rate there, with a Wilson interval, estimates what the budget never sees; recorded as `escalation_reason = budget \| random` (v2 signal) |
| Outcome | A VLM positive re-enters fusion; still suspicious goes to the review queue |
| Report per deployment | frames, blanks, escalated, recovered, random-sample miss rate, spend, `cost_per_recovered_FN` |

## 14. Disagreement matrix

Proposed to ww-backend as a view; `domain/disagreement.py` computes the same in Python
meanwhile. Edge comes from `media_evidence` because edge rows exist only for positives.

```sql
-- One verdict per source per media: 1 = animal, 0 = blank, NULL = no verdict.
create or replace view media_verdicts with (security_invoker = true) as
select
  m.id            as media_id,
  m.deployment_id,
  max(case when o.source_type = 'ai' and o.ai_origin = 'cloud'
            and o.source_model_version like 'speciesnet%'
           then (o.observation_type <> 'blank')::int end)                       as speciesnet,
  max(case when o.source_type = 'ai' and o.ai_origin = 'cloud'
            and o.source_model_version like 'gemini%'
           then (o.observation_type <> 'blank')::int end)                       as gemini,
  max(case when e.signal = 'edge_presence'    then e.value::int end)            as edge,
  max(case when e.signal = 'motion_frac'      then (e.value >= 0.02)::int end)  as motion,
  max(case when o.source_type = 'consensus'
           then (o.observation_type <> 'blank')::int end)                       as consensus,
  max(case when o.source_type = 'human'
            or o.review_status in ('human_reviewed', 'expert_reviewed', 'consensus_approved')
           then (o.observation_type <> 'blank')::int end)                       as human,
  max(case when e.signal = 'evidence_score'   then e.value end)                 as evidence_score
from media m
left join observations o
  on o.media_id = m.id and o.deleted_at is null and o.observation_level = 'media'
left join media_evidence e
  on e.media_id = m.id
where m.deleted_at is null
group by m.id, m.deployment_id;

-- The matrix for one deployment: every combination, how often, and how often a human agreed.
select speciesnet, gemini, edge, motion, consensus, human, count(*) as frames
from media_verdicts
where deployment_id = :deployment_id
group by 1, 2, 3, 4, 5, 6
order by frames desc;
```

| Metric | From the matrix |
|---|---|
| Per-model recall | `human = 1` rows split by each model's column |
| Combined recall | `human = 1` rows, `consensus` column |
| Suspicious-only recall | the same, restricted to `0.25 <= evidence_score < 0.50` |
| v2 weights | fitted to the counts of each pattern with `human` not NULL |

## 15. False-negative dataset

Every frame where a presence model said blank and a human said animal; append-only, dated, and
a benchmark run cites its snapshot.

```text
fn-dataset/                              (Supabase Storage bucket, private)
  index.csv
  <deployment_id>/<media_id>.jpg         (the full frame, never a crop)
  snapshots/<YYYY-MM-DD>/index.csv       (frozen copies used by a benchmark run)
```

| Column | Meaning |
|---|---|
| `media_id`, `deployment_id`, `burst_id` | identity |
| `missed_by` | semicolon list of `speciesnet`, `gemini`, `edge`, `motion`, `consensus` |
| `recovered_by` | the signal or stage that flipped it, or `human` |
| `taxonomy` | `ir_low_contrast`, `camouflage`, `partial_body`, `small_subject`, `motion_blur`, `rain`, `vegetation`, `border`, `other` |
| `strata` | the automatic strata of section 10 |
| `human_label`, `labelled_by`, `labelled_at`, `notes` | as in `label_presence.py` |

```sql
select o_model.media_id, o_model.source_model_version as missed_by
from observations o_model
join observations o_human
  on o_human.media_id = o_model.media_id and o_human.deleted_at is null
 and (o_human.source_type = 'human'
      or o_human.review_status in ('human_reviewed', 'expert_reviewed', 'consensus_approved'))
 and o_human.observation_type = 'animal'
where o_model.source_type = 'ai' and o_model.observation_type = 'blank'
  and o_model.deleted_at is null;
```

## 16. Learned cascade

Last in priority.

| | Contract |
|---|---|
| Target | Among SpeciesNet blanks, `y = 1` if a human confirmed an animal (FN dataset plus human-labelled blanks that stayed blank) |
| Inputs | `motion_frac`, `edge_score`, `burst_len`, `burst_animal_count`, `burst_index`, `speciesnet_max_conf`, DINO anomaly (`media_embeddings.is_outlier`, `cluster_confidence`), border proximity, quality class, neighbour agreement, the deployment's measured miss rate, time of day |
| Model | Logistic regression first; gradient boosting only if it wins on the stratified benchmark |
| Output | `fn_risk` in [0, 1], stored as `evidence_score` with `evidence_weights_version = 'learned_v1'` |
| Cut points | Per deployment, from the escalation budget, maximising expected recovered FN per dollar |
| Guard | A stratum with under 30 positives keeps the hand weights |
| Test set | The random low-risk sample of section 13, never trained on |

## 17. Not yet, and open items

Not yet: no vote and no Gemini replacing SpeciesNet (section 1); no auditor-only wiring; no
self-hosted VLM; no box fusion, species on the consensus row, DINO attention, quality
classifier or learned classifier in this iteration; no hand-tuning beyond the v1 guesses; no
change to `events.py`; no `observations` schema change from this repo.

Issues to file (none filed yet; the two drafts are beside this report):

| # | Repo | Issue | Acceptance criterion |
|---|---|---|---|
| 4 | ww-website | Stratified benchmark: the section 10 columns and human strata in `label_presence.py` and `eval_presence.py` | Recall with `n` and interval for every stratum with 30 or more animal frames |
| 5 | ww-website | Image-quality stage (`domain/image_quality.py`) writing `quality_class` | Every frame in a run has a `quality_class` row; `night_ir` and `motion_blur` agree with humans on at least 90% of a 200-frame check |
| 6 | ww-website | Localisation recovery: VLM bbox, motion ROI, DINO attention, crop, classifier; `crop_method` recorded (`detector`, `vlm_bbox`, `motion_roi`, `dino_attention`) | A consensus-animal frame with no detector box gets a crop and a species row naming the link that produced the box |
| 7 | ww-website + ww-backend | Disagreement matrix: `domain/disagreement.py` now, `media_verdicts` view to ww-backend | Every verdict combination with count and human-agreement share per deployment; v2 weights derived and recorded here |
| 8 | ww-website | Human-review loop: FN dataset, taxonomy field in the review UI, snapshot export | A human flipping a blank to animal produces an `index.csv` row with `missed_by` and `taxonomy` |
| 9 | ww-website | Escalation budget and random sample, behind a flag | Per-deployment report as in section 13; calls never exceed `B` |
| 10 | ww-website | Learned FN-risk classifier as `learned_v1` | Suspicious-only recall at the same budget at or above v1's, per stratum |
| P1 | ww-backend | `media_evidence` table, [`issue-ww-backend-media-evidence.md`](issue-ww-backend-media-evidence.md) | Table, unique constraint, RLS and pgTAP merged; the website writer's skip path no longer fires on dev |
| P2 | firmware | Capture-sequence EXIF tag, [`issue-firmware-sequence-tag.md`](issue-firmware-sequence-tag.md) | Frames of one trigger carry one trigger id and their index; `exif.py` parses it; the grouper uses it |
| P3 | ww-backend | `ai_models` row for the Gemini presence model (September report §6.7) | A Gemini-only run records `annotation_runs` without `annotation_run_record_failed` |
| P4 | ww-website | Record `speciesnet_max_conf` before the threshold filter; done in `SpeciesNetStep`*, file only if that branch does not merge | Every SpeciesNet blank has the row, 0 when the detector returned nothing |
| P5 | ww-website | Per-crop refinement writes its own row instead of patching SpeciesNet's | After a per-crop run the SpeciesNet row's `classified_by` is unchanged and a classifier row exists beside it |
| P6 | ww-website | Frontend badge and `emit_detection_notifications` read the consensus row for presence | Both follow the consensus row when one exists, today's precedence otherwise |
| P7 | ww-website | One grouper (decided 2026-09-30): `media_registry.group_bursts` and the labeller delegate to `burst_evidence.group_bursts` | `MOTION_ROI_BURST_GAP_SECONDS` removed; one `BURST_GAP_SECONDS`, default 10 |
| P8 | ww-website | GPU seconds per frame per run: `duration_seconds / media_processed` from `PipelineStepResult` in a run log line | Every run logs seconds per frame for each GPU step |

Open items:

- `auto_annotate_deployments` calls `reflect_edge_deployment` after `run_pipeline`, so
  `edge_presence` is absent at fusion time on that path (September report §6.8.4).
- `burst_index` from the tag is `frame_index - 1` (section 5); the vlm branch uses `frame_index`
  as is. Align before the firmware tag ships.
- Unmeasured: the Azure T4 per-second rate, L4 seconds per image, DINOv3 time per frame,
  contact-sheet cost on real burst lengths, and the placeholders 0.02 (motion saturation) and
  2% / 20% (size strata).
- Cached v1 verdicts: 632 is the cache copy saved beside the September report on 2026-09-28,
  664 is the scratchpad cache after the 2026-09-29 resume; both are prompt v1, superseded by v2.
