# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Job dispatch — route a job to the Cloud Run job, the ARQ worker, or run it in-process.

Single seam used by GPU/ML-heavy endpoints (the Wildlife Brain) so the routers
don't care *where* a job runs. First match wins:

- ``CLOUD_RUN_JOB_NAME`` set → start one execution of that Cloud Run job, which runs
  ``python -m app.jobs.cloudrun_entry <job> <json>`` on the ML image (Google Cloud
  pilot, report 2026-09_gcp-pilot-and-migration §2). Any failure to start it falls
  through to the next route, so a job is never dropped.
- ``REDIS_URL`` set  → enqueue to Redis; the ``embedding-worker`` (``--profile gpu``,
  ``target: worker`` with the heavy ML stack) picks it up. The lean API image never
  imports torch/hdbscan/umap.
- neither → fall back to the in-process asyncio runner (``runner.py``).
  Suitable for the ``dev`` image, which bundles the ML stack.

Job status is shared across processes via the Supabase ``api_jobs`` mirror in
``store.py`` (the API's ``get_job`` falls back to Supabase), so progress polling
works regardless of which process executed the job.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import timedelta

import structlog

from app.config import settings

logger = structlog.get_logger()

# KEDA scale-to-zero marker. ARQ's own queue is a Redis *sorted set*, which KEDA's
# stock ``redis`` scaler (LLEN on a list) cannot read — so on every offload we also
# push the ARQ job id onto this plain LIST, and the worker LREMs it on completion
# (see app/jobs/worker.py). KEDA scales the GPU worker on this list's length.
GPU_PENDING_KEY = "ww:gpu:pending"

# Largest job payload sent as a container-argument override. Cloud Run caps the size of a
# run request's overrides, and a CamtrapDP import can pass thousands of media ids; above
# this the job takes the Redis or in-process route instead.
CLOUD_RUN_MAX_PAYLOAD_BYTES = 32_000


async def enqueue_job(name: str, *args, **kwargs) -> str:
    """Dispatch a job by its definition function name.

    ``name`` must match a function in ``app.jobs.definitions`` (and, for the ARQ
    path, be registered in ``app.jobs.worker.WorkerSettings.functions``).

    ``**kwargs`` are forwarded to ARQ's ``enqueue_job`` — e.g. ``_defer_by`` (delay
    execution) and ``_job_id`` (dedup). These let callers debounce a burst of enqueues.
    The Cloud Run path honours ``_defer_by`` by starting the execution that much later
    from a background task; the in-process fallback runs immediately. Both forward only
    the real job kwargs to the function.

    Returns the routing mode actually used ("cloudrun", "arq" or "local") for logging/telemetry.
    """
    if settings.CLOUD_RUN_JOB_NAME:
        func_kwargs = _job_kwargs(kwargs)
        delay = _seconds(kwargs.get("_defer_by"))
        if delay > 0:
            # The api_jobs row stays 'queued' meanwhile, so later upload chunks reuse it.
            from app.jobs.runner import enqueue_local_job

            enqueue_local_job(_start_cloud_run_later(delay, name, args, func_kwargs))
            logger.info("job_dispatch_deferred_cloudrun", job=name, delay_s=delay)
            return "cloudrun"
        try:
            await start_cloud_run_execution(name, args, func_kwargs)
            return "cloudrun"
        except Exception as exc:
            logger.warning("cloudrun_dispatch_failed_fallback", job=name, error=str(exc))

    return await _enqueue_arq_or_local(name, *args, **kwargs)


async def _start_cloud_run_later(delay: float, name: str, args: tuple, func_kwargs: dict) -> None:
    await asyncio.sleep(delay)
    try:
        await start_cloud_run_execution(name, args, func_kwargs)
    except Exception as exc:
        logger.warning("cloudrun_dispatch_failed_fallback", job=name, error=str(exc))
        # The debounce has already been waited out, so no _defer_by on the fallback.
        await _enqueue_arq_or_local(name, *args, **func_kwargs)


async def start_cloud_run_execution(name: str, args: tuple, func_kwargs: dict) -> str:
    """Start one execution of ``CLOUD_RUN_JOB_NAME`` for this job; returns the execution name.

    Only the start is awaited: the execution reports progress through ``api_jobs`` like the
    ARQ worker, and the API never waits for it to finish.
    """
    payload = cloud_run_payload(args, func_kwargs)
    if len(payload.encode()) > CLOUD_RUN_MAX_PAYLOAD_BYTES:
        raise ValueError(f"job payload is {len(payload.encode())} bytes, over the {CLOUD_RUN_MAX_PAYLOAD_BYTES}-byte override limit")
    if not settings.GOOGLE_CLOUD_PROJECT:
        raise ValueError("GOOGLE_CLOUD_PROJECT is not set")
    job_path = f"projects/{settings.GOOGLE_CLOUD_PROJECT}/locations/{settings.CLOUD_RUN_JOB_REGION}/jobs/{settings.CLOUD_RUN_JOB_NAME}"
    request = _run_job_request(job_path, ["-m", "app.jobs.cloudrun_entry", name, payload])
    operation = await asyncio.to_thread(lambda: _jobs_client().run_job(request=request))
    execution = getattr(getattr(operation, "metadata", None), "name", "") or ""
    logger.info("job_dispatched_cloudrun", job=name, execution=execution)
    return execution


def cloud_run_payload(args: tuple, func_kwargs: dict) -> str:
    """The JSON ``cloudrun_entry`` reads back: ``{"args": [...], "kwargs": {...}}``."""
    return json.dumps({"args": list(args), "kwargs": func_kwargs}, separators=(",", ":"))


def _job_kwargs(kwargs: dict) -> dict:
    """Real job kwargs only: drop the ARQ control kwargs (_defer_by, _job_id, ...)."""
    return {k: v for k, v in kwargs.items() if not k.startswith("_")}


def _seconds(delay) -> float:
    if isinstance(delay, timedelta):
        return delay.total_seconds()
    return float(delay or 0)


# ── Google Cloud SDK seams (replaced in tests; imported lazily) ───────


def _jobs_client():
    from google.cloud import run_v2

    return run_v2.JobsClient(credentials=_trigger_credentials())


def _trigger_credentials():
    """``CLOUD_RUN_TRIGGER_SA_JSON`` as a path or inline JSON; None = Application Default Credentials."""
    raw = settings.CLOUD_RUN_TRIGGER_SA_JSON.strip()
    if not raw:
        return None
    from google.oauth2 import service_account

    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
    if os.path.isfile(raw):
        return service_account.Credentials.from_service_account_file(raw, scopes=scopes)
    return service_account.Credentials.from_service_account_info(json.loads(raw), scopes=scopes)


def _run_job_request(job_path: str, container_args: list[str]):
    from google.cloud import run_v2

    container = run_v2.RunJobRequest.Overrides.ContainerOverride(args=container_args)
    return run_v2.RunJobRequest(name=job_path, overrides=run_v2.RunJobRequest.Overrides(container_overrides=[container]))


async def _enqueue_arq_or_local(name: str, *args, **kwargs) -> str:
    if settings.REDIS_URL:
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
                        await pool.lpush(GPU_PENDING_KEY, job.job_id)
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
    enqueue_local_job(func(*args, **_job_kwargs(kwargs)))
    logger.info("job_enqueued_local", job=name)
    return "local"
