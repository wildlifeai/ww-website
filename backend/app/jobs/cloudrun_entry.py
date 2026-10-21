# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Entry point of the ML worker as a Cloud Run job: one execution runs one job, then exits.

    python -m app.jobs.cloudrun_entry <job_name> '<json>'   run a registered job
    python -m app.jobs.cloudrun_entry selftest              report the GPU and load the models

``<json>`` is ``{"args": [...], "kwargs": {...}}``, as ``dispatch.start_cloud_run_execution``
writes it. Jobs report progress and failure through ``api_jobs`` themselves, like on the ARQ
worker; the exit code only tells Cloud Run whether the process ran: 0 done, 1 the job or the
self-test failed, 2 bad arguments. The job is created with ``--max-retries 0``, so a failed
run is reported to the user, never repeated.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Callable, Optional

import structlog

logger = structlog.get_logger()

USAGE = "usage: python -m app.jobs.cloudrun_entry <job_name> '<json>' | selftest"


def parse_payload(raw: str) -> tuple[list, dict]:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("the payload must be a JSON object")
    args, kwargs = payload.get("args", []), payload.get("kwargs", {})
    if not isinstance(args, list) or not isinstance(kwargs, dict):
        raise ValueError("'args' must be a list and 'kwargs' an object")
    return args, kwargs


def resolve_job(name: str) -> Optional[Callable]:
    """Only registered jobs run, the same list the ARQ worker serves."""
    from app.jobs.definitions import JOBS

    return next((fn for fn in JOBS if fn.__name__ == name), None)


async def run_job(func: Callable, args: list, kwargs: dict) -> None:
    from app.jobs.store import flush_pending_syncs

    try:
        await func(*args, **kwargs)
    finally:
        # The process exits next: land every detached api_jobs sync first, or a finished
        # job could stay 'processing' until the stale-job reaper fails it.
        await flush_pending_syncs()


def selftest() -> bool:
    """Log the torch build, the GPU, and each model's load time (the cold start, t_cold)."""
    import torch

    cuda = torch.cuda.is_available()
    device = torch.cuda.get_device_name(0) if cuda else "none"
    print(f"torch {torch.__version__}, cuda? {cuda}, device {device}", flush=True)
    logger.info("selftest_gpu", torch=torch.__version__, cuda=cuda, device=device)

    from app.services.bioclip_service import get_bioclip_service
    from app.services.dinov3 import get_dinov3_service
    from app.services.speciesnet_service import get_speciesnet_service

    # The services load lazily on first inference; these are their loaders.
    loaders = {
        "speciesnet": lambda: get_speciesnet_service()._get_model(),
        "bioclip": lambda: get_bioclip_service()._get_tol_model(),
        "dinov3": lambda: get_dinov3_service()._load(),
    }
    ok = cuda
    started = time.monotonic()
    for model, load in loaders.items():
        t0 = time.monotonic()
        try:
            load()
            logger.info("selftest_model_loaded", model=model, seconds=round(time.monotonic() - t0, 1))
        except Exception as exc:
            ok = False
            logger.error("selftest_model_failed", model=model, error=str(exc))
    logger.info("selftest_models_ready", seconds=round(time.monotonic() - started, 1), ok=ok)
    return ok


def main(argv: list[str]) -> int:
    if argv == ["selftest"]:
        return 0 if selftest() else 1
    if len(argv) != 2:
        print(USAGE, file=sys.stderr)
        return 2
    name, raw = argv
    func = resolve_job(name)
    if func is None:
        logger.error("cloudrun_unknown_job", job=name)
        return 2
    try:
        args, kwargs = parse_payload(raw)
    except ValueError as exc:
        logger.error("cloudrun_bad_payload", job=name, error=str(exc))
        return 2

    logger.info("cloudrun_job_started", job=name)
    t0 = time.monotonic()
    try:
        asyncio.run(run_job(func, args, kwargs))
    except Exception as exc:
        logger.error("cloudrun_job_failed", job=name, error=str(exc), seconds=round(time.monotonic() - t0, 1))
        return 1
    logger.info("cloudrun_job_finished", job=name, seconds=round(time.monotonic() - t0, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
