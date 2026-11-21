#!/bin/bash -e
# Pull a new GPU image into a SIF, as a CPU Slurm job (the cluster docs run Apptainer builds as
# jobs, with the cache on nobackup). Submitted by ww_cron.sh with IMAGE and SIF through
# --export; the GPU job waits on it with --dependency=afterok. Keeps the two newest SIFs, so a
# rollback to the previous tag needs no pull.
#SBATCH --account=wildlife03546
#SBATCH --time=00:45:00
# 21 Nov 2026: with 8G and the default /tmp, mksquashfs was OOM-killed on the 3.8 GB image;
# 32G with unpacking on nobackup (below) built it in 20 minutes.
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --output=/nesi/project/wildlife03546/ww/slurm_logs/%x_%j.out

: "${IMAGE:?IMAGE not set}"
: "${SIF:?SIF not set}"
echo "$(date -Is) pulling $IMAGE into $SIF"

# Apptainer is on the system path (its module is retired), so no module is loaded.

# Cache and unpacking both on nobackup, not the compute node's /tmp.
export APPTAINER_CACHEDIR=/nesi/nobackup/wildlife03546/ww/apptainer-cache
export APPTAINER_TMPDIR=/nesi/nobackup/wildlife03546/ww/apptainer-tmp
mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR" "$(dirname "$SIF")"

# The image is public on GHCR, so the pull needs no credential.
apptainer pull --force "$SIF.partial" "docker://$IMAGE"
mv "$SIF.partial" "$SIF"

# shellcheck disable=SC2012 # names are ours, no spaces
ls -1t "$(dirname "$SIF")"/ww-backend-gpu-*.sif | tail -n +3 | xargs -r rm -f
echo "$(date -Is) done: $(ls -lh "$SIF" | awk '{print $5}')"
