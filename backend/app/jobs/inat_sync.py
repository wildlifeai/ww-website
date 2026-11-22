# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The iNaturalist community-ID sync as a job (#455).

``POST /api/inat/sync`` starts ``sync_inat_job`` and answers with its id. Up to 200
observations, each an update and a few reads and writes, can outrun API Gateway's 30 s on AWS,
so the work runs on the ``cpu`` function there (``WORKER_BACKEND=lambda``) and in the API
process otherwise (``dispatch.IN_PROCESS_JOBS``), never on the GPU worker. The sync reads
iNaturalist's public API only, so the job carries the user's id and no token. The job lists
leave it out (``store.UNLISTED_KINDS``): it runs at each sign-in, and Settings follows it by id.
"""

from __future__ import annotations

import time
from typing import Any, Dict

import structlog

from app.jobs.store import update_job
from app.schemas.job import JobStatus

logger = structlog.get_logger()

# At most one progress write this often: each is a Supabase write.
_PROGRESS_EVERY_SECONDS = 2.0


def sync_message(r: Dict[str, Any]) -> str:
    """The finished job's message, which Settings shows as the sync's answer."""
    return (
        f"Synced {r['updated']} of {r['checked']}: {r['research']} research grade, "
        f"{r['disagreement']} disagreement, {r['observations_written']} community IDs written."
    )


async def sync_inat_job(job_id: str, user_id: str) -> None:
    """Pull community identifications for one user's published observations (``domain/inaturalist_sync.py``)."""
    from app.domain.inaturalist import INatDomainError
    from app.domain.inaturalist_sync import sync_inat_identifications

    logger.info("job_start", job_type="inat_sync", job_id=job_id)
    await update_job(job_id, status=JobStatus.PROCESSING, progress=0.05, message="Asking iNaturalist for community IDs…")
    last_write = 0.0

    async def on_progress(done: int, total: int) -> None:
        nonlocal last_write
        if time.monotonic() - last_write < _PROGRESS_EVERY_SECONDS:
            return
        last_write = time.monotonic()
        await update_job(job_id, progress=0.1 + 0.9 * done / max(total, 1), message=f"Checked {done} of {total} observations…")

    try:
        result = await sync_inat_identifications(user_id=user_id, progress=on_progress)
    except INatDomainError as e:
        await update_job(job_id, status=JobStatus.FAILED, error=str(e))
        logger.warning("job_failed", job_type="inat_sync", job_id=job_id, error=str(e))
        return
    except Exception as e:
        await update_job(job_id, status=JobStatus.FAILED, error="The sync failed. Try again later.")
        logger.error("job_failed", job_type="inat_sync", job_id=job_id, error=str(e))
        return

    await update_job(job_id, status=JobStatus.COMPLETED, progress=1.0, message=sync_message(result))
    logger.info("job_complete", job_type="inat_sync", job_id=job_id, checked=result["checked"])
