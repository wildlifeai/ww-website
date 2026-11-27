# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-HTTP Lambda events, which the Lambda Web Adapter POSTs to ``/events``.

Mounted only on Lambda (``WW_LAMBDA_ROLE`` set). Two kinds:

- a job, ``{"ww-job": "<definition>", "args": [...], "kwargs": {...}}`` or with
  ``payload_key`` for a payload spilled to the object store (``services/lambda_invoke.py``).
  Run to completion here, on the ``cpu`` function only;
- the scheduled tick, ``{"ww-tick": true}`` from EventBridge Scheduler: the stale-job reaper,
  the nightly label checks (``jobs/label_checks.py``, once a day) and one ``batch_queue`` log
  line (queued jobs, oldest age) that a CloudWatch metric filter turns into the "oldest queued"
  alarm. Harmless if called over HTTP, so any role accepts it.

A failed job answers 500, which makes the invoke count as failed, so the template's
on-failure destination (the dead-letter queue) keeps the event.
"""

from __future__ import annotations

import json
import os
import time

import structlog
from fastapi import APIRouter, HTTPException, Request

from app.config import settings

logger = structlog.get_logger()

router = APIRouter(include_in_schema=False)


async def run_job_event(event: dict) -> None:
    """Run the job an event names, in this process."""
    from app.jobs import definitions
    from app.services.lambda_invoke import EVENT_MARKER

    name = str(event[EVENT_MARKER])
    func = getattr(definitions, name, None)
    if func is None or func not in definitions.JOBS:
        raise HTTPException(status_code=400, detail=f"Unknown job: {name}")
    args, kwargs = event.get("args") or [], event.get("kwargs") or {}
    if event.get("payload_key"):
        from app.services import object_store

        data, _meta = await object_store.retrieve_blob(str(event["payload_key"]))
        if data is None:
            raise HTTPException(status_code=410, detail="Job payload is gone")
        body = json.loads(data)
        args, kwargs = body.get("args") or [], body.get("kwargs") or {}
    logger.info("lambda_job_start", job=name)
    await func(*args, **kwargs)
    if event.get("payload_key"):
        from app.services import object_store

        await object_store.delete_blob(str(event["payload_key"]))


def emf_line(oldest_minutes: float, queued_jobs: int) -> str:
    """One CloudWatch Embedded Metric Format line: Lambda's log stream turns it into the
    ``WildlifeWatcher/BatchOldestQueuedMinutes`` metric the template alarms on."""
    return json.dumps(
        {
            "_aws": {
                "Timestamp": int(time.time() * 1000),
                "CloudWatchMetrics": [
                    {
                        "Namespace": "WildlifeWatcher",
                        "Dimensions": [["Environment"]],
                        "Metrics": [{"Name": "BatchOldestQueuedMinutes", "Unit": "None"}, {"Name": "BatchQueuedJobs", "Unit": "Count"}],
                    }
                ],
            },
            "Environment": os.environ.get("WW_ENV", "dev"),
            "BatchOldestQueuedMinutes": oldest_minutes,
            "BatchQueuedJobs": queued_jobs,
        }
    )


async def run_tick() -> dict:
    """The reaper, and the queue's age for the alarm."""
    from app.domain.batch_lease import pending_summary
    from app.jobs.label_checks import maybe_queue_nightly
    from app.jobs.store import reap_stale_jobs

    reaped = await reap_stale_jobs(max_age_minutes=60)
    await maybe_queue_nightly()
    summary = await pending_summary() if settings.WORKER_BACKEND == "lambda" else {"jobs": 0, "oldest_minutes": 0}
    logger.info("batch_queue", queued_jobs=summary["jobs"], oldest_minutes=summary["oldest_minutes"], reaped=reaped)
    print(emf_line(float(summary["oldest_minutes"]), int(summary["jobs"])), flush=True)
    return {"reaped": reaped, "queued_jobs": summary["jobs"], "oldest_minutes": summary["oldest_minutes"]}


@router.post("/events")
async def events(request: Request):
    from app.services.lambda_invoke import EVENT_MARKER

    event = await request.json()
    if isinstance(event, dict) and event.get("ww-tick"):
        return await run_tick()
    if isinstance(event, dict) and EVENT_MARKER in event:
        if settings.WW_LAMBDA_ROLE != "cpu":
            raise HTTPException(status_code=403, detail="Jobs run on the cpu function")
        await run_job_event(event)
        return {"ran": event[EVENT_MARKER]}
    raise HTTPException(status_code=400, detail="Unrecognised event")
