# Stages: base (lean deps) → api (DEPLOYED, lean uvicorn API) | dev (base+ML+reload) | worker (base+ML, ARQ)
# | lambda (base+CPU clustering, Lambda Web Adapter; AWS) | gpu (worker, batch_infer; REANNZ).
# The deployed API is the `api` stage — build it with `--target api`. The `worker`
# stage runs the ARQ GPU worker (`arq …`) and serves no HTTP; never deploy it as the API.
FROM python:3.11-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]

# --- Dev target (base + test tooling + heavy ML so local in-process embedding works) ---
FROM base AS dev
COPY requirements-dev.txt requirements-ml.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt -r requirements-ml.txt
# OpenCV (pulled in by speciesnet/yolov5) loads these system libs at runtime —
# without them inference fails with e.g. `libxcb.so.1: cannot open shared object file`.
# Placed AFTER pip so adding/changing it doesn't invalidate the heavy ML pip layer cache.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libxcb1 \
    && rm -rf /var/lib/apt/lists/*
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# --- Embedding worker target (base + heavy ML; GPU at runtime) ---
# Runs the ARQ worker that processes embedding/inference jobs off the lean API.
# Activating offload also requires Redis + switching enqueue to ARQ (see jobs/worker.py).
FROM base AS worker
COPY requirements-ml.txt .
RUN pip install --no-cache-dir -r requirements-ml.txt
# OpenCV system libs (see dev target) — required for speciesnet/yolov5 inference.
# After pip so it doesn't invalidate the ML pip layer cache.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libxcb1 \
    && rm -rf /var/lib/apt/lists/*
CMD ["arq", "app.jobs.worker.WorkerSettings"]

# --- Lambda target (AWS, WORKER_BACKEND=lambda): the api and cpu functions -----------
# One arm64 image for both; the SAM template (infra/aws/template.yaml) sets WW_LAMBDA_ROLE.
# The Lambda Web Adapter extension runs uvicorn behind the Lambda runtime API and passes
# non-HTTP events (job invokes, the scheduled tick) to POST /events (app/lambda_events.py).
# requirements-lambda.txt adds the CPU clustering stack the cpu function needs, no torch.
FROM base AS lambda
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter
COPY requirements-lambda.txt .
RUN pip install --no-cache-dir -r requirements-lambda.txt
# Buffered: API Gateway's HTTP API fronts the api function (no Function URLs in ap-southeast-6)
# and invokes it without streaming, as the cpu function's job invokes do. Lambda's filesystem is
# read-only outside /tmp, where numba (UMAP, HDBSCAN) must keep its cache: without it the embed
# ingest failed with "cannot cache function" (2026-11-21).
ENV NUMBA_CACHE_DIR=/tmp/numba-cache \
    PORT=8000 \
    AWS_LWA_PORT=8000 \
    AWS_LWA_READINESS_CHECK_PATH=/health \
    AWS_LWA_INVOKE_MODE=buffered \
    AWS_LWA_PASS_THROUGH_PATH=/events
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# --- GPU target (REANNZ cluster, through Apptainer): app/jobs/batch_infer.py ----------
# The worker's ML stack with the batch entry point. No database settings, no AWS key, no
# model weights: cluster/reannz/ww_infer.sl mounts the weights Victor cached once and sets
# HF_HUB_OFFLINE. Published publicly on GHCR (the code is GPL and public already).
FROM worker AS gpu
# Apptainer starts in the caller's directory, not WORKDIR, so the app package needs PYTHONPATH.
# The SIF is read-only, so numba (UMAP, HDBSCAN) keeps its cache under /tmp, as on Lambda.
ENV HF_HUB_OFFLINE=1 \
    PYTHONPATH=/app \
    NUMBA_CACHE_DIR=/tmp/numba-cache
CMD ["python", "-m", "app.jobs.batch_infer", "--help"]

# --- API target (DEPLOYED) — the lean production HTTP API ----------------------
# Kept LAST so that even a `docker build` with no --target produces the API (uvicorn)
# rather than the ARQ `worker` above, which serves no HTTP. CMD inherited from base
# and re-declared here for clarity.
FROM base AS api
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]

