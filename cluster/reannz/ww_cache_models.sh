#!/bin/bash
# Cache the model weights once under the project directory, so ww_infer.sl runs offline.
# Run by Victor on a login node, after ww_pull.sl has built the SIF:
#
#   /nesi/project/wildlife03546/ww/bin/ww_cache_models.sh /nesi/project/wildlife03546/ww/sif/ww-backend-gpu-<tag>.sif
#
# Asks for a Hugging Face token (DINOv3 is a gated model) without echoing it, passes it to one
# CPU job through the environment, and forgets it when the job ends. No agent sees the token.
set -euo pipefail

SIF="${1:?usage: ww_cache_models.sh <path to the SIF>}"
ROOT="${WW_ROOT:-/nesi/project/wildlife03546/ww}"
mkdir -p "$ROOT/models/hf" "$ROOT/models/kaggle" "$ROOT/models/torch"

read -rs -p 'HF_TOKEN: ' HF_TOKEN
echo
export APPTAINERENV_HF_TOKEN="$HF_TOKEN"
unset HF_TOKEN

# Downloads allowed here only; the GPU image runs offline by default. PYTHONPATH and --pwd
# cover SIFs built before the image set PYTHONPATH itself.
export APPTAINERENV_HF_HUB_OFFLINE=0 APPTAINERENV_HF_HOME=/models/hf \
  APPTAINERENV_KAGGLEHUB_CACHE=/models/kaggle APPTAINERENV_TORCH_HOME=/models/torch \
  APPTAINERENV_EMBEDDING_DEVICE=cpu APPTAINERENV_BIOCLIP_DEVICE=cpu APPTAINERENV_PYTHONPATH=/app

srun --account=wildlife03546 --cpus-per-task=4 --mem=16G --time=00:45:00 \
  apptainer exec --pwd /app --bind "$ROOT/models:/models,$ROOT/bin:/wwbin" "$SIF" \
  python /wwbin/ww_cache_models.py

du -sh "$ROOT"/models/*
