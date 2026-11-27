# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""When label checks are recomputed (#448, design decision 11): for a deployment's project after
each Wildlife Brain run, nightly for the projects whose labels changed that day, and on request
(``POST /api/label-checks/{project_id}/recompute``). The work is ``domain/label_check_runs.py``;
the jobs themselves are ``label_checks_job`` and ``nightly_label_checks_job`` in definitions.py.

Everything here is a no-op while ``FF_LABEL_CHECKS_ENABLED`` is off, and never raises into the
Brain run or the tick that calls it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog

from app.config import settings

logger = structlog.get_logger()

JOB_KIND = "label_checks"
NIGHTLY_KIND = "label_checks_nightly"


async def queue_project(project_id: str, user_id: Optional[str] = None) -> str:
    """One recompute of a project, as a job; returns its id. No deployment ids on the job, so the
    grid shows no "being processed" banner for it."""
    from app.jobs.dispatch import enqueue_job
    from app.jobs.store import create_job

    job_id = await create_job(user_id=user_id, kind=JOB_KIND, label=f"Label checks, project {project_id[:8]}")
    await enqueue_job("label_checks_job", job_id, project_id)
    return job_id


async def queue_after_brain_run(deployment_ids: list[str]) -> None:
    """After a Wildlife Brain run: a recompute for each project of ``deployment_ids``."""
    if not settings.FF_LABEL_CHECKS_ENABLED or not deployment_ids:
        return
    try:
        from app.domain.label_check_runs import projects_of_deployments
        from app.services.supabase_client import create_service_client

        for project_id in await asyncio.to_thread(projects_of_deployments, create_service_client(), deployment_ids):
            await queue_project(project_id)
    except Exception as exc:
        logger.warning("label_checks_queue_failed", deployments=len(deployment_ids), error=str(exc))


def nightly_due(now: datetime, last_run: Optional[str]) -> bool:
    """Whether the nightly recompute should start: in its hour, and not yet today (UTC)."""
    if now.hour != settings.LABEL_CHECK_NIGHTLY_HOUR_UTC:
        return False
    return last_run is None or datetime.fromisoformat(last_run) < now.replace(hour=0, minute=0, second=0, microsecond=0)


def _last_nightly() -> Optional[str]:
    from app.services.db_utils import rows_of
    from app.services.supabase_client import create_service_client

    query = create_service_client().table("api_jobs").select("id, created_at").eq("job_data->>kind", NIGHTLY_KIND)
    rows = rows_of(query.order("created_at", desc=True).limit(1).execute())
    return rows[0]["created_at"] if rows else None


async def maybe_queue_nightly(now: Optional[datetime] = None) -> Optional[str]:
    """From the reaper's loop and the Lambda tick: the nightly job, once a day. The lock keeps two
    API replicas from both starting it; its own ``api_jobs`` row says it ran today."""
    if not settings.FF_LABEL_CHECKS_ENABLED:
        return None
    now = now or datetime.now(timezone.utc)
    if now.hour != settings.LABEL_CHECK_NIGHTLY_HOUR_UTC:
        return None
    try:
        from app.jobs.dispatch import enqueue_job
        from app.jobs.store import create_job
        from app.services.locks import LockBusy, exclusive

        try:
            async with exclusive("label-checks-nightly", wait=False, ttl_seconds=120):
                if not nightly_due(now, await asyncio.to_thread(_last_nightly)):
                    return None
                job_id = await create_job(kind=NIGHTLY_KIND, label="Label checks, nightly")
        except LockBusy:
            return None
        since = (now - timedelta(days=1)).isoformat()
        await enqueue_job("nightly_label_checks_job", job_id, since)
        return job_id
    except Exception as exc:
        logger.warning("label_checks_nightly_failed", error=str(exc))
        return None
