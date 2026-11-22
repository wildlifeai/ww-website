#!/bin/bash
# The REANNZ login-node cron for one environment: ask the API whether cloud AI work is waiting
# and, if so, submit one ww_infer.sl Slurm job (pulling a new GPU image first when the API names
# one this cluster has not seen). Plain code from Victor's own crontab, not an agent: the
# cluster's AI agent rules (from 1 November 2026) do not cover it, and no agent touches the token.
#
#   */30 * * * * /nesi/project/wildlife03546/ww/bin/ww_cron.sh prod >> /nesi/project/wildlife03546/ww/logs/cron-prod.log 2>&1
#
# The interval must match the API's BATCH_CRON_INTERVAL_MINUTES (default 30): the website adds it
# to BATCH_MAX_WAIT_MINUTES when it tells people how long AI and grouping usually wait.
#
# Reads, per environment, from ~/.config/ww/ (mode 700, files 600, the home directory rather
# than the project directory, which other project members can read):
#   <env>.token   the lease token; the API keeps only its SHA-256 (WORKER_TOKEN_SHA256)
#   <env>.api     the API base URL, one line (the ww-<env> stack's ApiUrl output)
# Design and runbook: documentation/development reports/2026-10_aws-reannz-migration/README.md
set -euo pipefail

ENV="${1:?usage: ww_cron.sh dev|prod}"
ROOT="${WW_ROOT:-/nesi/project/wildlife03546/ww}"
CONF="${WW_CONF:-$HOME/.config/ww}"
TOKEN_FILE="$CONF/$ENV.token"
API="$(head -n 1 "$CONF/$ENV.api")"
API="${API%/}"
JOB_NAME="ww-$ENV-infer"
# cron does not always set USER.
ME="${USER:-$(id -un)}"

# Every line timestamped, so a run can be matched to the API's logs.
log() { echo "$(date -Is) [$ENV] $*"; }

# The token goes to curl through a file descriptor, never on a command line that `ps` shows
# to other users of the login node (printf is a shell builtin).
api_get() {
  curl -fsS --max-time 30 -H @<(printf 'Authorization: Bearer %s\n' "$(cat "$TOKEN_FILE")") "$API$1"
}

if squeue -h -u "$ME" -n "$JOB_NAME" -o %i | grep -q .; then
  log "a $JOB_NAME job is already queued or running"
  exit 0
fi

PENDING="$(api_get /api/worker/pending)"
# The API's answer is one flat JSON object of numbers, booleans and comma-free strings.
field() { sed -n "s/.*\"$1\": *\"\{0,1\}\([^\",}]*\)\"\{0,1\}.*/\1/p" <<<"$PENDING"; }
SUBMIT="$(field submit)"
IMAGE="$(field image)"
IMAGE="${IMAGE:--}"
MINUTES="$(field time_limit_minutes)"
PHOTOS="$(field photos)"
JOBS="$(field jobs)"
log "queued jobs=$JOBS photos=$PHOTOS submit=$SUBMIT image=$IMAGE"
[ "$SUBMIT" = true ] || exit 0
if [ "$IMAGE" = "-" ]; then
  log "the API names no GPU image (/ww/$ENV/GPU_IMAGE is unset); nothing submitted"
  exit 1
fi

TAG="${IMAGE##*:}"
SIF="$ROOT/sif/ww-backend-gpu-$TAG.sif"
DEPENDENCY="singleton"
if [ ! -f "$SIF" ]; then
  PULL_NAME="ww-pull-$TAG"
  PULL="$(squeue -h -u "$ME" -n "$PULL_NAME" -o %i | head -n 1)"
  if [ -z "$PULL" ]; then
    PULL="$(sbatch --parsable --job-name "$PULL_NAME" --export=ALL,IMAGE="$IMAGE",SIF="$SIF" "$ROOT/bin/ww_pull.sl")"
    log "pulling $IMAGE as job $PULL"
  fi
  DEPENDENCY="afterok:$PULL,singleton"
fi

JOB="$(sbatch --parsable --job-name "$JOB_NAME" --time "$MINUTES" --dependency "$DEPENDENCY" \
  --export=ALL,SIF="$SIF",WW_API="$API",WW_ENV="$ENV",WW_GPU_IMAGE="$IMAGE" "$ROOT/bin/ww_infer.sl")"
log "submitted $JOB_NAME as job $JOB (--time ${MINUTES} min, dependency $DEPENDENCY)"
