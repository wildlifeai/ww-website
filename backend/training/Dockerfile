# Species Brain trainer: the Cloud Run job image (backend/training/README.md).
#
# Base: the official TensorFlow GPU image for the version pinned in requirements.txt. The
# recipe (MobileNetV2 0.35, 96 px) also trains on CPU, so the same image serves the CPU
# job and local runs (`docker run` without `--gpus`). No Vela here: the website compiles.
#
# Build and push to the migration plan's Artifact Registry repo (not run from the website):
#   gcloud builds submit backend/training \
#     --tag asia-southeast1-docker.pkg.dev/${PROJECT}/ww-backend/ww-species-trainer:$(git rev-parse --short HEAD)
FROM tensorflow/tensorflow:2.21.0-gpu

WORKDIR /trainer

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY dataset_utils.py gcs_io.py train.py ./

# Local-folder defaults; each Cloud Run execution overrides both
# (app/services/gcp_training.py::job_environment).
ENV TRAINING_INPUT_URI=/data/input \
    TRAINING_OUTPUT_URI=/data/output \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "train.py"]
