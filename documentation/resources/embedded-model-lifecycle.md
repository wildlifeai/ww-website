# Embedded AI Model Lifecycle (end-to-end)

> The full path a custom on-device model travels — from training to the device
> running it in the field to its predictions appearing on the website — and which
> repository owns each stage. This is the **map**; each stage links to the detailed
> doc that owns it.

Repos: **website** (`ww-website`), **backend** (`ww-backend`, canonical schema),
**mobile** (`wildlife-watcher-mobile-app`), **firmware** (`Seeed_Grove_Vision_AI_Module_V2`).

```
 (1) Train/export        (2) Upload & convert      (3) Label mapping      (4) Manifest
   Edge Impulse   ──►   website /api/models/  ──►  website label_map ──► website MANIFEST.zip
   int8 .tflite zip      convert → Vela → .TFL       (species ↔ taxon)     (.TFL + labels.txt)
   (by hand, or the website drives it: Annotations → Actions → Create species ID model…, which
    covers stages 1 to 3 in one job; see stage 1)
                                                                                 │
 (8) Reflect (planned)  (7) Ingest EXIF        (6) On-device inference    (5) Deploy
   edge observations ◄─ website exif.py    ◄──  firmware: load+run    ◄──  mobile (BLE) or
   beside SpeciesNet     UserComment parse       write EXIF UserComment      SD-card manifest
```

## What the device can run

The `ww500_md` firmware is a **classifier pipeline**, and every stage below assumes it:

- It reads the output tensor as a `[1, C]` vector, softmaxes it into per-class percentages,
  and copies the first `C` bytes into a result buffer of `MAX_CLASSES = 16`
  (`cvapp.cpp`, `image_task.c`). So **`C` must be at most 16**.
- It reports **class index 1 as the target** (`processNNOutput`, with its own
  `// TODO This only works for the person detection`). So a two-class model is
  `[background, target]` in that order: `no person` / `person`, `not rat` / `rat`.
- It has no box decoding or NMS. A detection head (`[1, 84, 756]` and the like) is
  read as a class vector and overflows the buffer
  ([firmware #225](https://github.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/issues/225)).
- The labels file has **one line per class, in class order, LF-terminated**; the device
  names a class with no line as an empty string.

The website enforces the parts it can see: LM-1 compares the labels list to a classifier's
output shape at upload (stage 2), and the pretrained registry marks the detection models
`blocked` so they cannot be selected or packaged. **Detection models are future work**,
gated on the two classifiers, person detection and rat detection, running end to end
first. The firmware side of the same contract (refusing to load, rather than warning, on a
class/label mismatch) is #225.

## 1. Train & export (user · Edge Impulse)
A **fully int8-quantized** TFLite classifier. The Edge Impulse "TensorFlow Lite
(int8 quantized)" export contains `trained.tflite` + label metadata. Float or
partially-quantized models fail Vela compilation (see stage 2).

Two ways to get there:

- **By hand in Edge Impulse**, following the recipe in the Notion page *Machine Learning
  Models* (96×96 grayscale, MobileNetV2 0.35 transfer learning, 30 to 50 cycles), then the
  Toolkit upload in stage 2.
- **From the website** (behind `FF_MODEL_TRAINING_ENABLED`): select labelled images on the
  Annotations page, *Actions → Create species ID model…*. `POST /api/models/train` builds the
  dataset (human labels beat Cloud AI ones, camera labels are never used), drives the same
  recipe through the Edge Impulse API, downloads the int8 export and continues with stages 2
  and 3 automatically, `label_map` included. With no Edge Impulse credentials configured it
  packages the dataset as a ZIP for a manual run instead. Details, limits and the class-order
  trap: [species-brain-training-spec](../development%20reports/species-brain-training-spec.md).

## 2. Upload & convert (website → backend)
`POST /api/models/convert` → `convert_model_job` → `convert_uploaded_model`
([`backend/app/domain/model.py`](../../backend/app/domain/model.py)) extracts the
`.tflite`, compiles it for the Ethos-U55 NPU via **Vela**
([`services/vela.py`](../../backend/app/services/vela.py)), and stores the `.TFL` +
`.TXT` in the Supabase Storage `ai-models` bucket. An `ai_models` row is created
(status lifecycle `uploading → uploaded → validated`/`failed`).
Detail: [AI Model & Manifest Pipeline](./ai-model-pipeline.md).

Robustness notes (all current): the converter accepts any `.tflite` in the ZIP
(not only `trained.tflite`), treats `model_variables.h` as optional, ignores macOS
`__MACOSX`/`._` shadow files, surfaces the real Vela error (e.g. "FullyConnected
could not run on NPU" = not int8-quantized), and uses unique `_pending/{job_id}`
placeholder paths to satisfy the `NOT NULL UNIQUE` path columns
(see backend [path-columns design issue](../../../ww-backend/documentation/development%20reports/ai-models-path-columns-design-issue.md)).

**Label integrity is enforced here**, because upload is the last point where the
model and its labels are seen together. Conversion is refused when the labels list
is not as long as the model's output tensor (**LM-1**, read from the TFLite
flatbuffer, so it also covers a precompiled `.tfl` that carries no Edge Impulse
metadata), when a precompiled `labels.txt` disagrees with `model_variables.h` in
count or order (**LM-2**), and when the metadata contradicts its own declared class
count. A model whose output shape cannot be read is allowed through: that is a
missing cross-check, not a mismatch. Class order is never sorted, because device
class index *i* must resolve to `labels[i]`.

> LM-1 was originally implemented against the header's self-declared count only,
> which a precompiled package without a header slips past. That is how a two-class
> "Person Detection (96x96)" model shipped with a one-line labels file: the device
> named class 1 with an empty string, so every `UserComment` on that deployment
> read `'': 70%` and no prediction could be mapped to a taxon
> ([#134](https://github.com/wildlifeai/ww-website/issues/134)).

## 3. Label mapping (website)
After validation, [`ModelLabelMapper`](../../frontend/src/components/toolkit/ModelLabelMapper.tsx)
asks the uploader what each output class means — **target species** (mapped to a
taxon via `SpeciesPicker`) or **background/negative** (e.g. `not rat`) — and saves it
to `ai_models.label_map` (jsonb; RLS: organisation_manager). Class order comes from
the model's own `detection_capabilities`, so it stays aligned with the device's
`labels.txt` (stage 4). This is what lets stage 8 reflect `rat: 87%` as *Rattus
rattus* and skip negatives. Disambiguates labels like a bare `rat`.

## 4. Manifest / Prepare SD Card (website)
`generate_manifest` assembles `MANIFEST.zip` containing the model + `labels.txt` +
camera config + Himax firmware, named `{firmware_model_id}V{version_number}.TFL`
(8.3 format). `labels.txt` must be written in the model's class order so device
class index *i* ↔ `labels[i]` ↔ `label_map`. Detail:
[AI Model & Manifest Pipeline](./ai-model-pipeline.md#manifest-generation).

## 5. Deploy to device (mobile / SD card → backend)
Either the **mobile app** transfers the model over BLE (`loadmodel`, OP 14
`firmware_model_id` + OP 15 `version_number`) or the user copies `MANIFEST.zip` to
the SD card. Only `validated`/`deployed` models sync. Detail:
[mobile AI-Model-Integration](../../../ww-mobile-app/documentation/resources/AI-Model-Integration.md).

## 6. On-device inference (firmware)
The device loads the model + `labels.txt` from the SD-card manifest, runs inference,
and writes three things into each JPEG's EXIF:
- **Deployment ID** → tag `0xF200`
- **AE telemetry** → `MakerNote` (`0x927C`), `"integration, analogGain, digitalGain, aeMean, converged"`
- **NN class scores** → `UserComment` (`0x9286`), `"<label>: <pct>%; …"`

> `USE_PERCENTAGE` is **enabled** on firmware `dev` (`cvapp.h:36`), so percentages are
> written today. Whether the contract stays percentages or moves to logits is being
> decided in [firmware #189](https://github.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/issues/189);
> the website's ingest (stage 7) reads percentages, so a change there needs a change here.

## 7. Ingest EXIF (website)
On SD-card upload, [`backend/app/domain/exif.py`](../../backend/app/domain/exif.py)
parses `UserComment` into `user_comment_fields` (`{"rat": 87}`) and extracts the
deployment ID. The **cloud SpeciesNet pipeline** runs independently on the same
images ([04-AI-PIPELINE](../onboarding/04-AI-PIPELINE.md)) — the device and cloud
models are complementary, not exclusive.

> The parser strips NUL bytes from the result (`_strip_nul`): `UserComment` carries
> an 8-byte NUL-padded charset code (`ASCII\0\0\0…`) and Postgres `jsonb` rejects the
> NUL codepoint (22P05), which would otherwise fail media registration for every
> device frame. See [dual-ai-production-rollout §6](./dual-ai-production-rollout.md).

## 8. Reflect on-device prediction (website · shipped on dev, behind a flag)
[`backend/app/domain/edge_reflection.py`](../../backend/app/domain/edge_reflection.py)
turns `user_comment_fields` into observations tagged `ai_origin='edge'` (with
`source_model_version = '{firmware_model_id}V{version_number}'`, matching the deployed
`.TFL` filename), mapped to taxa via `label_map`; background/negative classes and
sub-threshold scores are skipped. Runs inside the Drive upload job as soon as the media rows
are registered (before the cloud pipeline, and regardless of the `run_ai` opt-out; the camera
already decided in the field, so its result should not wait minutes for SpeciesNet), and again
after the annotation pipeline (`auto_annotate_deployments`, idempotent), replace-don't-append
like the SpeciesNet step. The annotation panel shows *📟 Camera AI: rat 87%* beside
*☁ Cloud AI: Rattus rattus 64%* (`AiOriginBadge`, driven by `observations.ai_origin`), and a
*📟 Camera AI on the device* line above the observations lists the raw per-class scores of
every frame (`lib/cameraScores.ts`), so a frame the camera judged *no person 62%* still shows
the device's decision even though nothing is reflected for it.
Gated on `FF_EDGE_REFLECT_ENABLED`. The ww-backend `dual_ai_v0` migration
(`observations.ai_origin` + `device_alert_rules`) is **merged** and the flag is on
**on dev**; real end-to-end value still depends on the firmware fix (stage 6).

## Open items
- **End to end, person detection first.** Nothing has yet travelled the whole path on a
  real device with a correctly labelled model: the run is upload via the pretrained path →
  `label_map` → deploy over BLE from the mobile app (website SD-card prep as the backup) →
  capture → upload → edge observation beside the Cloud AI one. Rat detection follows once
  [ww-backend #175](https://github.com/wildlifeai/ww-backend/issues/175) gives it its own
  labels file (its seeded `7V1.txt` is a person-detection leftover).
- **Firmware:** [#225](https://github.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/issues/225)
  (refuse to load on class/label mismatch; clamp the result copy) and
  [#189](https://github.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/issues/189) (percentages
  vs logits in `UserComment`, which stage 7 depends on).
- **Redeploying a corrected labels file** needs the card sync to actually retransfer it:
  the sync only sends files the card is missing, so the old `.TXT` has to be deleted from
  `/MANIFEST/` or the card reformatted
  ([#134](https://github.com/wildlifeai/ww-website/issues/134)).
- **Website-trained model, end to end.** The Annotations training path (stage 1) has not yet
  run against a live Edge Impulse project or been loaded on a camera; that run, on the rat
  images, is the next step ([species-brain-training-spec](../development%20reports/species-brain-training-spec.md)).
- **Detection models** (YOLOv8/YOLOv11 OD, YOLOv8 Pose) stay `blocked` in the registry until
  the firmware can run a detection head; see *What the device can run* above.
- **Rollout:** promote schema (ww-backend `main`) + app (staging) + enable the flag per
  [dual-ai-production-rollout](./dual-ai-production-rollout.md); coordinate `ai_origin`
  with mobile.
- ✅ **Backend:** `dual_ai_v0` schema merged; `FF_EDGE_REFLECT_ENABLED` on (dev).
- ✅ **Frontend:** `ai_origin='edge'` rows surfaced with the 📟 Camera-AI badge.
- ✅ **Backend:** `ai_models.model_path`/`labels_path` nullable (the `_pending`
  placeholder is retired).

Naming note: the canonical terms are **Camera AI** (user-facing) / **Edge AI**
(technical) for this on-device layer and **Cloud AI** for the website pipeline — see
[AI-ARCHITECTURE](./AI-ARCHITECTURE.md).
