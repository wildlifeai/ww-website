# Species Brain trainer (the Cloud Run job)

The container behind `MODEL_TRAINER=gcp`: a manifest of labelled images in, an int8 TFLite
classifier out. Vela, the tensor-arena check and LM-1 run on the website, not here. The job
contract (settings, GCS layout, manifest schema, outputs, identities) and the recipe are in
[2026-09_gcp-native-model-training](../../documentation/development%20reports/2026-09_gcp-native-model-training/README.md).

| File | What it does |
|---|---|
| `train.py` | Load images, MobileNetV2 (alpha 0.35) transfer learning, evaluation, full-integer int8 quantisation, `metrics.json` |
| `dataset_utils.py` | Config (manifest recipe, then CLI flags), the manifest's items and split, class weights, crop geometry (pure, tested) |
| `gcs_io.py` | `gs://` download and upload with Application Default Credentials; local paths pass through |
| `tests/` | `pytest backend/training/tests` (the smoke test skips without TensorFlow) |

## Run it locally

Put the images and a `manifest.json` (schema in the report) in one folder, then:

```bash
cd backend/training
python -m venv venv && source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt                       # TensorFlow 2.21; CPU is fine at 96 px
python train.py --input ./dataset --output ./out --epochs 30
```

Flags override the manifest's recipe or the container's defaults: `--image-size`, `--colour`,
`--epochs`, `--learning-rate`, `--batch-size`, `--no-augmentation`, `--no-class-weights`,
`--no-pretrained` (random init, for offline tests).

With Docker, which is what Cloud Run runs:

```bash
docker build -t ww-species-trainer backend/training
docker run --rm -v "$PWD/dataset:/data/input" -v "$PWD/out:/data/output" ww-species-trainer
# add --gpus all for a local NVIDIA GPU
```

## Deploy (not run from this repository)

Project, region, Artifact Registry repo and identities come from the
[migration plan](../../documentation/development%20reports/2026-09_gcp-pilot-and-migration/README.md);
the job flags are in the report's container table.

```bash
PROJECT=ww-pilot-dev  REGION=asia-southeast1
IMG=$REGION-docker.pkg.dev/$PROJECT/ww-backend/ww-species-trainer:$(git rev-parse --short HEAD)
gcloud builds submit backend/training --tag $IMG
gcloud run jobs create ww-species-trainer --image $IMG --region $REGION \
  --cpu 4 --memory 8Gi --task-timeout 3600 --max-retries 0 \
  --service-account ww-trainer-job@$PROJECT.iam.gserviceaccount.com
```
