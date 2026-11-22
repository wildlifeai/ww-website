# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Job dispatch: route a job to the ARQ worker (GPU offload), the REANNZ batch, or this process.

Single seam used by GPU/ML-heavy endpoints (the Wildlife Brain) so the routers
don't care *where* a job runs:

- ``REDIS_URL`` set  → enqueue to Redis; the ``embedding-worker`` (``--profile gpu``,
  ``target: worker`` with the heavy ML stack) picks it up. The lean API image never
  imports torch/hdbscan/umap.
- ``REDIS_URL`` empty → fall back to the in-process asyncio runner (``runner.py``).
  Suitable for the ``dev`` image, which bundles the ML stack.

Job status is shared across processes via the Supabase ``api_jobs`` mirror in
``store.py`` (the API's ``get_job`` falls back to Supabase), so progress polling
works regardless of which process executed the job.
"""

from __future__ import annotations

import structlog

from app.config import settings

logger = structlog.get_logger()

# KEDA scale-to-zero marker. ARQ's own queue is a Redis *sorted set*, which KEDA's
# stock ``redis`` scaler (LLEN on a list) cannot read — so on every offload we also
# push the ARQ job id onto this plain LIST, and the worker LREMs it on completion
# (see app/jobs/worker.py). KEDA scales the GPU worker on this list's length.
GPU_PENDING_KEY = "ww:gpu:pending"

# GPU jobs the REANNZ batch runs under WORKER_BACKEND=lambda, with the kind of lease each
# needs: ``annotate`` (originals through SpeciesNet and BioCLIP) or ``embed`` (crops through
# DINOv3). The batch path is in domain/batch_lease.py, jobs/batch_infer.py and jobs/ingest.py.
BATCH_JOBS: dict[str, str] = {
    "annotate_deployments_job": "annotate",
    "embed_deployment_job": "embed",
    "reprocess_deployment_job": "embed",
    "embed_deployments_job": "embed",
}

# Jobs that run in the API process today, whatever REDIS_URL says. Under
# WORKER_BACKEND=lambda every job that is not a batch job goes to CPU_FUNCTION_NAME.
IN_PROCESS_JOBS = frozenset(
    {
        "upload_drive_images_job",
        "generate_manifest_job",
        "convert_model_job",
        "download_pretrained_job",
        "download_github_pretrained_job",
        "ingest_results_part_job",
        "finalize_batch_job",
        "sync_inat_job",
    }
)


async def _queue_for_batch(name: str, args: tuple, kwargs: dict) -> str:
    """Leave the job's ``api_jobs`` row queued with a dispatch record the cluster's lease reads."""
    from datetime import datetime, timedelta, timezone

    from app.jobs.store import set_job_dispatch

    job_id, *rest = args
    now = datetime.now(timezone.utc)
    defer = kwargs.get("_defer_by")
    not_before = now + (defer if isinstance(defer, timedelta) else timedelta(seconds=float(defer or 0)))
    spec = {
        "backend": "batch",
        "name": name,
        "kind": BATCH_JOBS[name],
        "args": list(rest),
        "kwargs": {k: v for k, v in kwargs.items() if not k.startswith("_")},
        "queued_at": now.isoformat(),
        "not_before": not_before.isoformat(),
        "attempts": 0,
    }
    if not await set_job_dispatch(str(job_id), spec):
        raise ValueError(f"No api_jobs row for batch job {job_id}")
    logger.info("job_queued_for_batch", job=name, job_id=job_id, kind=spec["kind"])
    return "batch"


async def enqueue_job(name: str, *args, **kwargs) -> str:
    """Dispatch a job by its definition function name.

    ``name`` must match a function in ``app.jobs.definitions`` (and, for the ARQ
    path, be registered in ``app.jobs.worker.WorkerSettings.functions``).

    ``**kwargs`` are forwarded to ARQ's ``enqueue_job`` — e.g. ``_defer_by`` (delay
    execution) and ``_job_id`` (dedup). These let callers debounce a burst of enqueues.
    The ARQ control kwargs (_defer_by, _job_id) only apply on the Redis path; the in-process
    fallback runs immediately, ignoring those but forwarding any real job kwargs to the function.

    Returns the routing mode actually used ("batch", "lambda", "arq" or "local") for logging/telemetry.

    Routing: under ``WORKER_BACKEND=lambda`` a :data:`BATCH_JOBS` name waits in ``api_jobs`` for
    the REANNZ batch and every other job goes to ``CPU_FUNCTION_NAME``. Otherwise an
    :data:`IN_PROCESS_JOBS` name runs here, as it always has, and the rest go to ARQ when
    ``REDIS_URL`` is set.
    """
    if settings.WORKER_BACKEND == "lambda":
        if name in BATCH_JOBS:
            return await _queue_for_batch(name, args, kwargs)
        if settings.CPU_FUNCTION_NAME:
            from app.jobs.store import flush_pending_syncs
            from app.services.lambda_invoke import invoke_job

            # create_job writes the job's api_jobs row in the background, and the cpu function
            # looks for that row as soon as it starts. Let the write land before the invoke.
            await flush_pending_syncs()
            await invoke_job(name, args, {k: v for k, v in kwargs.items() if not k.startswith("_")})
            logger.info("job_invoked_lambda", job=name, function=settings.CPU_FUNCTION_NAME)
            return "lambda"
    elif settings.REDIS_URL and name not in IN_PROCESS_JOBS:
        try:
            from arq import create_pool
            from arq.connections import RedisSettings

            pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
            try:
                job = await pool.enqueue_job(name, *args, **kwargs)
                # Mirror a pending marker onto the KEDA-readable list (best-effort:
                # a missing marker only affects autoscaling, never correctness).
                if job is not None:
                    try:
                        # redis-py types each command as `Awaitable[int] | int` for both clients.
                        await pool.lpush(GPU_PENDING_KEY, job.job_id)  # pyright: ignore[reportGeneralTypeIssues]
                    except Exception as exc:
                        logger.debug("gpu_pending_marker_push_failed", job=name, error=str(exc))
            finally:
                await pool.aclose()
            logger.info("job_enqueued_arq", job=name)
            return "arq"
        except Exception as exc:
            # Redis unreachable / arq missing → don't lose the job, run it locally.
            logger.warning("arq_enqueue_failed_fallback_local", job=name, error=str(exc))

    # In-process fallback. Imports the definition lazily so the API image only
    # pulls heavy ML modules if it actually executes the job here.
    from app.jobs import definitions
    from app.jobs.runner import enqueue_local_job

    func = getattr(definitions, name, None)
    if func is None:
        raise ValueError(f"Unknown job: {name}")
    # Forward real job kwargs; drop ARQ control kwargs (_defer_by/_job_id, etc.),
    # which only apply to the Redis/ARQ path.
    func_kwargs = {k: v for k, v in kwargs.items() if not k.startswith("_")}
    enqueue_local_job(func(*args, **func_kwargs))
    logger.info("job_enqueued_local", job=name)
    return "local"
