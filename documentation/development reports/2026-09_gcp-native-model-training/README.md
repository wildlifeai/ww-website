# Species Brain training on Google Cloud (native trainer)

> **Status:** 📋 Proposal with code, 2026-09-29. Uncommitted on `feat/gcp-native-training-v2`,
> stacked on [#151](https://github.com/wildlifeai/ww-website/pull/151) (the Edge Impulse
> trainer, [species-brain-training-spec](../species-brain-training-spec.md)). No Google Cloud
> resource exists and nothing has trained through this path: every cost and duration below is
> an estimate. Google Cloud foundations (projects, IAM, Workload Identity, Artifact Registry,
> regions, prices, NZD rate) are owned by the
> [Google Cloud pilot and migration plan](../2026-09_gcp-pilot-and-migration/README.md)
> (branch `docs/gcp-pilot-and-migration`); this report only adds what training needs. Training
> is stage 2.9 of that plan, after the 16 October worker pilot, not part of it.

## Decisions

| Topic | Decision, and why |
|---|---|
| Split of work | The container trains and stops at the int8 TFLite; the worker compiles with `services/vela.py::run_vela_conversion` and registers through `convert_uploaded_model`'s precompiled branch, so a trained model passes the same checks as an uploaded one |
| Dataset and split | #151's `build_training_dataset` and `split_samples`; the manifest carries each item's split and the container never re-splits |
| Trainer selection | `training_mode()` returns `gcp` when `FF_NATIVE_TRAINING_ENABLED` and `MODEL_TRAINER=gcp`; #151's job branches on it. No trainer interface: one implementation did not earn one |
| Compute | Cloud Run job, because it runs the same container per execution, bills per second and scales to zero; Vertex AI custom training bills machine hours plus a management fee for no gain at this size |
| Machine | CPU job (4 vCPU / 8 GiB) by default; an L4 variant of the same image (4 vCPU / 16 GiB + L4) for 160 px or 3,000-image runs, chosen by `TRAINING_JOB_NAME` |
| Region, project, registry, identity | The migration plan's: asia-southeast1, `GOOGLE_CLOUD_PROJECT`, `CLOUD_RUN_JOB_REGION`, Artifact Registry repo `ww-backend`, Application Default Credentials (no key setting) |
| Sydney option | A CPU-only job could run in australia-southeast1, nearer Supabase (the plan already runs Cloud Run there); it would need its own region setting, not added |
| Arena check | In `services/vela.py` for every model source (Toolkit upload, Edge Impulse, gcp): Vela's SRAM estimate against `MODEL_ARENA_BYTES` |
| LM-1 | Only in `convert_uploaded_model` (precompiled branch reads the compiled `.tfl`) |
| Checks only this path needs | `domain/trainer.py::validate_artifacts`: label order, int8 input and output, the grayscale input contract |
| Vela version | `ethos-u-vela==5.2.0` pinned in `backend/requirements.txt` (was `>=3.10`); only the website image runs Vela now |

## Job contract

### Settings (`backend/app/config.py`; set on the worker as well as the API)

| Setting | Default | Meaning |
|---|---|---|
| `FF_NATIVE_TRAINING_ENABLED` | `false` | Off: `POST /api/models/train` behaves exactly as #151 |
| `MODEL_TRAINER` | `edge_impulse` | `edge_impulse` or `gcp` |
| `GOOGLE_CLOUD_PROJECT` | `""` | Shared with the migration plan (`ww-pilot-dev`, later `ww-dev` / `ww-prod`) |
| `CLOUD_RUN_JOB_REGION` | `asia-southeast1` | Shared with the migration plan; job and bucket region |
| `GCS_TRAINING_BUCKET` | `""` | `ww-training-dev` / `ww-training`, named like the plan's `ww-uploads-dev` / `ww-uploads` |
| `TRAINING_JOB_NAME` | `ww-species-trainer` | The Cloud Run job |
| `TRAINING_POLL_INTERVAL_S` | `15` | Seconds between execution polls |
| `TRAINING_RUN_TIMEOUT_S` | `3600` | At most 3600: Cloud Run caps a GPU task at 1 hour |
| `MODEL_ARENA_BYTES` | `524288` | 512 KiB, the arena `ww500_md.ld` reserves (`. = . + 512K;`, firmware at `3ca2823c`) |

### GCS layout (`services/gcp_training.py`)

`run_key` is the `ai_models.id`, lowercased, so a retried job lands on the same prefix and
reuses a finished output.

```
gs://<GCS_TRAINING_BUCKET>/runs/<run_key>/dataset/manifest.json
gs://<GCS_TRAINING_BUCKET>/runs/<run_key>/dataset/images/<nnnnn>.jpg
gs://<GCS_TRAINING_BUCKET>/runs/<run_key>/execution.json     execution name + log URI (a restarted worker keeps polling)
gs://<GCS_TRAINING_BUCKET>/runs/<run_key>/output/{model_int8.tflite, model_float.keras, labels.txt, metrics.json}
```

Lifecycle rule: delete `runs/*/dataset/` after 30 days and `runs/*/output/` after 365; the
registered model lives in Supabase Storage.

### Manifest (`schema_version` 1, `domain/trainer.py::build_dataset_objects`)

```json
{
  "schema_version": 1,
  "run_key": "a3c7c373-...",
  "model_name": "Rat brain",
  "labels": ["not rat", "rat"],
  "recipe": {"image_size": 96, "colour": "grayscale", "epochs": 30, "learning_rate": 0.001},
  "items": [{"file": "images/00000.jpg", "label": "rat", "split": "train"}]
}
```

`labels` is `summary.labels` (background first) and is the output-tensor order. `recipe` is the
four fields of #151's `TrainModelRequest`; the container owns every other parameter.

### Container (`backend/training/`)

| | |
|---|---|
| Image | `asia-southeast1-docker.pkg.dev/<GOOGLE_CLOUD_PROJECT>/ww-backend/ww-species-trainer:<sha>` (`tensorflow/tensorflow:2.21.0-gpu`, `google-cloud-storage`, `Pillow`; no Vela) |
| Env per execution | `TRAINING_INPUT_URI=gs://…/runs/<run_key>/dataset`, `TRAINING_OUTPUT_URI=gs://…/runs/<run_key>/output`; nothing else |
| Outputs | `model_int8.tflite`, `model_float.keras`, `labels.txt` (class order, LF), `metrics.json` (`accuracy` = int8 held-out accuracy, float and int8 confusion matrices, per-class scores, history, recipe, `timings`, `versions`) |
| Exit | 0 on success, 1 with the reason on stdout |
| Job flags | `--task-timeout 3600 --max-retries 0`; CPU `--cpu 4 --memory 8Gi`; L4 `--cpu 4 --memory 16Gi --gpu 1 --gpu-type nvidia-l4 --no-gpu-zonal-redundancy` |
| Identities | Job runtime account `ww-trainer-job@`: `roles/storage.objectUser` on the bucket. The worker's account (`ww-ml-worker@` in the plan): `roles/storage.objectUser` on the bucket and `roles/run.developer` on the training job (a run with env overrides) |

Run it locally: [backend/training/README.md](../../../backend/training/README.md).

## Recipe parameters that differ from #151

Everything else is #151's [recipe table](../species-brain-training-spec.md#the-recipe-as-automated).

| Parameter | #151 (Edge Impulse) | gcp container |
|---|---|---|
| Resize | fit-short, squash | fit-short, centre crop |
| Head | Edge Impulse block, 16 neurons, dropout 0.1 | GlobalAveragePooling, Dropout 0.1, Dense 16 relu, Dense softmax |
| Grayscale with ImageNet weights | Edge Impulse's own handling (unverified) | 1 channel replicated to 3 inside the graph, so the pretrained stem loads unchanged |
| Augmentation | policy `all` | flip, brightness ±25, contrast 0.8 to 1.2, zoom-crop 85 to 100 % |
| Class weights | auto | balanced, `total / (n_classes * count)` |
| Quantisation | Edge Impulse int8 export | `TFLITE_BUILTINS_INT8`, int8 in and out, 200 representative images plus an all-0 and an all-255 frame |
| Class order | alphabetical (the class-order trap) | the manifest's `labels`, as selected |
| Fixed | | alpha 0.35, batch 32, seed 42 |

## Cost and wall clock per 1,000-image run (estimates)

Prices: the Tier 2 (Singapore) Cloud Run jobs rates and GCS operation prices in the
migration plan's [§6.1](../2026-09_gcp-pilot-and-migration/README.md#61-list-prices-read-on-2026-09-26-usd);
NZD at the plan's rate. Durations are not measured: the run is dominated by container start,
dataset download, int8 calibration and evaluation, since only the head trains (800 training
images, 30 epochs, frozen base). `metrics.json.timings` from the first run replaces them.

| Job | Wall clock | Compute | GCS (about 1,000 writes, 1,000 reads) | Total |
|---|---|---|---|---|
| CPU, 4 vCPU / 8 GiB | 8 to 12 min, costed at 12 | 720 s × (4 × 0.0000216 + 8 × 0.0000024) = US$0.076 | US$0.005 (Singapore operation prices unverified; the plan lists Sydney's) | **US$0.08, NZD 0.14** |
| L4, 4 vCPU / 16 GiB | 4 to 6 min, costed at 6 | 360 s × (0.00022404 + 4 × 0.0000216 + 16 × 0.0000024) = US$0.126 | US$0.005 | **US$0.13, NZD 0.23** |

Dataset storage (about 30 MB for 30 days) is under a tenth of a cent. The CPU job also falls
inside the monthly free tier listed in the same section until roughly 65 runs a month.

## Grayscale input contract

`cvapp.cpp::img_rescale` (ww500_md, firmware `3ca2823c`) writes width × height
single-channel int8 values of `pixel - 128`, whatever channel count the input tensor declares.
The model reads `x = scale × (q - zero_point)`; `validate_artifacts` accepts only these:

| Model input `[1, S, S, C]`, int8 | scale | zero point | x on the camera | Verdict |
|---|---|---|---|---|
| This container (Rescaling inside the graph) | 1.0 | -128 | pixel 0..255 | accepted |
| Edge Impulse image block | 1/255 | -128 | pixel 0..1 | accepted |
| `preprocess_input` outside the graph | 2/255 | -1 or 0 | pixel -1..1 | accepted |
| Any other (scale, zero point) | | | numbers the model never saw | refused |
| C = 3 (RGB) | | | only a third of the buffer written | refused for a grayscale recipe, warning for RGB |

## Changes to #151 made on this branch

1. `jobs/definitions.py::train_species_brain_job`: the `uploaded` status moved above the trainer
   branch; `if training_mode() == "gcp"` calls `jobs/native_training.py::train_on_gcp`; the Edge
   Impulse block is unchanged but indented under `else`, and its recipe, metrics and job ids are
   collected in `trained`, merged into the `processing_log` entry; accuracy is read from
   `trained["metrics"]`; one docstring line.
2. `domain/training.py::training_mode`: returns `gcp` when `FF_NATIVE_TRAINING_ENABLED` and
   `MODEL_TRAINER=gcp`.
3. `routers/models.py::train_model`: creates the `ai_models` row when the mode is not
   `export_only` (was: only `edge_impulse`); docstring of `train_status`.
4. `frontend/src/types/training.ts`: `TrainingMode` gains `gcp`.

Changes to code #151 inherits from dev: `services/vela.py` (arena check), `domain/model.py`
(public `read_io_tensors`, which `_classifier_class_count` now uses), `config.py`
(`MODEL_ARENA_BYTES`), `requirements.txt` (Vela pin). The spec needed no edit.

## Open items

- **First measured run** on the rat dataset: replaces the durations and costs above and
  compares int8 accuracy and bench behaviour with the Edge Impulse model.
- **The arena check now refuses models Vela says need more than 512 KiB** on every upload path.
  No model in the dev `ai-models` bucket has been measured against it; do that before merging.
- **Shared settings with the migration plan**: its §8 adds `GOOGLE_CLOUD_PROJECT` and
  `CLOUD_RUN_JOB_REGION` with default `""` and `google-cloud-run` in `requirements.txt`; this
  branch adds the two settings (region default `asia-southeast1`) and `google-cloud-run==0.16.1`
  in `requirements-ml.txt`. Whichever lands second reconciles.
- **Compose**: the new settings are not forwarded in `docker-compose.yml`.
- **Modal copy**: it still explains Edge Impulse's alphabetical class order, which does not
  apply to `gcp`.
- **Unverified**: `TFLiteConverter.from_keras_model` on a Keras 3 model in TensorFlow 2.21
  (`train.py` falls back to a SavedModel); the `gcloud run jobs create` GPU flag spelling;
  whether `roles/storage.objectUser` covers overwriting an object; Vela's `sram_memory_used` as
  the firmware's arena usage (read from Vela 5.2.0's `stats_writer.py`, not from a bench load).
