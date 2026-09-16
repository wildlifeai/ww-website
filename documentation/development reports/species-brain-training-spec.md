# Species Brain training from the Annotations page

> **Status:** 🔧 Active, built behind `FF_MODEL_TRAINING_ENABLED` (default off). Code merged to
> `dev` pending review; not yet run end to end against a live Edge Impulse project or on a camera.
> Owner: Victor. Serves the GDMA APAC 2026 key result "a non-technical user can train a new
> species identification model" (Tech objective, Multi-species Camera AI on the device).

## What it does

A project member selects images on the Annotations page, opens **Actions → Create species ID
model…**, ticks which species become classes, names the model and presses Train. The backend
then does what the team used to do by hand in Edge Impulse for the rat and person models
(recipe in the Notion page *Machine Learning Models*):

1. builds the dataset from the selected images and their observations,
2. uploads it to an Edge Impulse project, configures the impulse, generates features, trains a
   MobileNetV2 (0.35) transfer-learning classifier and builds the int8 export,
3. compiles the export for the Ethos-U55 with Vela and registers it as an `ai_models` row with
   `label_map` filled from the chosen classes,
4. offers to set it as the Species Brain of the projects the selection came from.

The result is exactly what the Toolkit upload path produces, so Prepare SD Card and the
mobile app load it without any change.

When no Edge Impulse credentials are configured the same action packages the dataset as an
Edge-Impulse-ready ZIP (one folder per class, a `dataset.json` and a README with the recipe)
and offers it for download, so a manual run in Edge Impulse stays possible.

## The recipe, as automated

Taken from the Notion page and from the person and rat models already deployed:

| Step | Manual (Notion) | Automated |
|---|---|---|
| Images per class | 100 to 1000 | Refuses below `MODEL_TRAINING_MIN_IMAGES_PER_CLASS` (20), warns below `MODEL_TRAINING_RECOMMENDED_IMAGES_PER_CLASS` (100) |
| Image size | 96×96 (or 160) | `image_size` 96 default, 160 optional |
| Colour | Grayscale | `colour` grayscale default, RGB optional |
| Resize | Fit shortest axis | `resizeMode` fit-short, `resizeMethod` squash |
| Learning block | Transfer learning, MobileNetV2 0.35 | `EDGE_IMPULSE_TRANSFER_MODEL` (`transfer_mobilenetv2_a35`), 16 neurons, dropout 0.1 |
| Training | 30 to 50 cycles, LR 0.001, augmentation on | `epochs` 30 default (5 to 100), LR 0.001, augmentation policy `all`, auto class weights |
| Validation | 20 % | 20 % held out as the testing set, hashed per image so reruns split the same way |
| Export | Deployment → Custom → int8 quantized ZIP | `POST /jobs/build-ondevice-model?type=<EDGE_IMPULSE_DEPLOY_FORMAT>` with `engine tflite`, `modelType int8` |
| Convert | Website Toolkit upload → Vela | Same `convert_uploaded_model` code path |

## Dataset rules (what a selected image turns into)

Decided in `backend/app/domain/training.py::assign_samples` and previewed in the modal by
`frontend/src/lib/trainingDataset.ts` with the same rules:

- Observations the camera itself produced (`ai_origin = 'edge'`) never count. Training on the
  model we are replacing would only reinforce its mistakes.
- When an image has a human observation, only human observations count; otherwise the Cloud AI
  ones do. The modal shows the human-reviewed share and warns below 50 %.
- One sample per usable observation: a **target** class when its `scientific_name` matches a
  chosen class, the **background** class when it is a blank or an unlisted species (only if the
  user keeps the background class on), nothing otherwise.
- The sample image is the observation crop when there is one (`crop_url`, else the media
  `animal_crop_url`), else the full frame downscaled to 320 px. Crops are what the recipe wants
  for a classifier; the full-frame fallback keeps blanks usable.
- Limits: at most `MODEL_TRAINING_MAX_IMAGES` (3000) samples and `MODEL_TRAINING_MAX_CLASSES`
  (16, the firmware `MAX_CLASSES`) classes including background.

## The class-order trap

The firmware reports class index 1 as "the target" of a two-class model, and Edge Impulse
orders classes alphabetically. A model whose classes are `gecko` and `not gecko` therefore
targets the wrong class on the camera. The modal defaults the background label to one that
sorts first (`not rat` for `rat`, else `other`, `background`, `_background`), the user can
change it, and after training `firmware_target_warning` records a warning in the job message
and in `processing_log` when index 1 is not a target class. The right fix stays in the firmware
([#225](https://github.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/issues/225)); see
[embedded-model-lifecycle](../resources/embedded-model-lifecycle.md#what-the-device-can-run).

## Pieces

| Piece | Where |
|---|---|
| Flag, Edge Impulse credentials, limits | `backend/app/config.py` (`FF_MODEL_TRAINING_ENABLED`, `EDGE_IMPULSE_*`, `MODEL_TRAINING_*`) |
| Request schema | `backend/app/schemas/model.py` (`TrainModelRequest`, `TrainClassSpec`) |
| Dataset builder, label map, warnings | `backend/app/domain/training.py` |
| Edge Impulse Studio + ingestion client | `backend/app/services/edge_impulse.py` (httpx, `x-api-key`, job polling) |
| Versioning and artefact storage shared with uploads | `backend/app/domain/model.py` (`next_model_version`, `store_model_artifacts`) |
| Worker job | `backend/app/jobs/definitions.py::train_species_brain_job` (registered in `JOBS`) |
| Endpoints | `backend/app/routers/models.py`: `GET /api/models/train/status`, `POST /api/models/train` |
| Action + modal | `frontend/src/components/data/MediaBulkActions.tsx`, `TrainModelModal.tsx`, wired in `MediaBrowser.tsx` |
| Selection preview | `frontend/src/lib/trainingDataset.ts` (+ vitest) |
| Types | `frontend/src/types/training.ts` |
| Compose | `docker-compose.yml` forwards the flag and credentials to **both** `api` and `embedding-worker` |

Access: the endpoint requires a verified user who manages the organisation that will own the
model (same rule as the Toolkit upload) and checks that every selected image sits in a
deployment the caller may read (`app.authz.accessible_deployment_ids`). The job runs on the
ARQ worker when `REDIS_URL` is set (Edge Impulse waits are long), in-process otherwise.

Status of the `ai_models` row during a run: created with `file_type = 'training'` and the
default status, `uploaded` once the dataset is built, `validated` (or `failed`) at the end,
with the dataset counts, recipe, Edge Impulse job ids and validation metrics appended to
`processing_log`. No schema change was needed; the Settings model picker was widened to show
both `validated` and `deployed` models.

## Tests

- `backend/tests/test_training_domain.py`: label sanitising, background label choice, sample
  assignment (edge ignored, human beats cloud, blanks, unlisted species), limits, the split,
  the dataset ZIP and the label map.
- `backend/tests/test_edge_impulse_service.py`: request shapes against a mock transport
  (headers, impulse and training payloads, error envelope, build and download parameters,
  polling, failure, timeout).
- `backend/tests/test_training_router.py`: the flag envelope, the manager gate, missing and
  inaccessible images, what is queued in each mode.
- `frontend/src/lib/trainingDataset.test.ts`: the selection preview.

## Not done yet

- **Run it for real.** Nothing has trained through a live Edge Impulse project yet. Needs a
  project API key and project id (`EDGE_IMPULSE_API_KEY`, `EDGE_IMPULSE_PROJECT_ID`) on the
  dev API and worker, then a run on the rat images and a load on a WW500. Check that the
  `custom` deployment format is available on the account (the job lists the formats and fails
  early with the available ones otherwise) and that the trained model passes Vela.
- **One Edge Impulse project is shared.** The job clears the project before each run, so two
  users training at the same time would collide. Fine for the accelerator cohort; a
  per-organisation project (or the Edge Impulse organisation API) is the fix if it becomes a
  problem.
- **Energy and multi-model** key results are separate work (firmware).
- **Deleted images.** A training run reads the images at submit time; nothing links the model
  back to the media rows except the counts in `processing_log`.
- Move this report to `_archive/` once the first model trained here has run on a camera in the
  field.
