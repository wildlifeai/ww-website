#!/bin/bash -e
# One REANNZ GPU job for one environment: lease cloud AI work from the API, run SpeciesNet,
# BioCLIP and DINOv3 in the GPU image (Apptainer), upload results parts, repeat until the API
# has nothing left. Submitted by ww_cron.sh, which passes --time sized to the queue, the
# job name ww-<env>-infer and SIF, WW_API, WW_ENV, WW_GPU_IMAGE through --export.
# No database credential and no AWS key reach this job: only the lease token.
# Lessons carried over from Spyfish (spyfish-aotearoa-toolkit run_pipeline.sl, train_job.sl).

# One job per environment at a time (Spyfish: never two jobs on the same survey). ww_cron.sh
# repeats it on the command line, which replaces this line when a pull job comes first.
#SBATCH --dependency=singleton
#SBATCH --account=wildlife03546
# L4 in genoa, the cheapest GPU and enough for inference. The Blackwell pro_6000 failed
# Spyfish's torch; an a100 means milan. h100 is in genoa too, for a long L4 queue.
#SBATCH --partition=genoa
#SBATCH --gpus-per-node=l4:1
# DINOv3 ViT-H was OOM-killed at 8 Gi on Azure. The L4 price example includes 4 GB: measure
# MaxRSS with sacct in the dev trial and trim this.
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=01:00:00
#SBATCH --output=/nesi/project/wildlife03546/ww/slurm_logs/%x_%j.out

PROJECT=/nesi/project/wildlife03546/ww
: "${SIF:?SIF not set (submit through ww_cron.sh)}"
: "${WW_API:?WW_API not set}"
: "${WW_ENV:?WW_ENV not set}"

echo "$(date -Is) [$WW_ENV] job $SLURM_JOB_ID on $(hostname), image ${WW_GPU_IMAGE:-unknown}"

# `module purge` on milan nodes drops the mahuika tree; re-adding it is a no-op on genoa.
module purge
module use /opt/nesi/lmod/mahuika
module load Apptainer

# nobackup is staging only (files untouched for 90 days are deleted); S3 is the record.
WORK=/nesi/nobackup/wildlife03546/ww/$WW_ENV/$SLURM_JOB_ID
mkdir -p "$WORK"
trap 'rm -rf "$WORK"' EXIT

# Weights cached once by hand under the project directory (backed up); the job never
# downloads them, so it needs no Hugging Face or Kaggle credential.
MODELS=$PROJECT/models
export APPTAINERENV_HF_HOME=/models/hf
export APPTAINERENV_HF_HUB_OFFLINE=1
export APPTAINERENV_KAGGLEHUB_CACHE=/models/kaggle
export APPTAINERENV_TORCH_HOME=/models/torch
export APPTAINERENV_EMBEDDING_DEVICE=cuda
export APPTAINERENV_BIOCLIP_DEVICE=cuda
export APPTAINERENV_WW_GPU_IMAGE="${WW_GPU_IMAGE:-}"
export APPTAINERENV_SLURM_JOB_ID="$SLURM_JOB_ID"

run() {
  apptainer exec --nv --bind "$WORK:/work,$MODELS:/models,$HOME/.config/ww:/conf:ro" "$SIF" "$@"
}

# A job that silently lost its GPU runs many times slower and shows only as a timeout: stop now.
run python -c "import torch, sys; ok = torch.cuda.is_available(); print('GPU:', torch.cuda.get_device_name(0) if ok else 'NONE'); sys.exit(0 if ok else 1)"

run python -m app.jobs.batch_infer --api "$WW_API" --token-file "/conf/$WW_ENV.token" --work /work

echo "$(date -Is) [$WW_ENV] job $SLURM_JOB_ID done"
