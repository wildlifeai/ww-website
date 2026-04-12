# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Job status store — Redis-backed read/write for async job state.

Every job has a key ``job:{id}`` in Redis containing its current status,
progress, result URL, and error (if any). TTL is 24 hours.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as redis

from app.config import settings
from app.schemas.job import JobStatus, JobInfo

JOB_TTL = 86400  # 24 hours


async def _get_redis() -> redis.Redis:
    """Create a Redis connection for job store operations."""
    return redis.from_url(settings.REDIS_URL)


async def create_job() -> str:
    """Create a new job entry and return its ID."""
    job_id = str(uuid.uuid4())
    r = await _get_redis()

    job_data = {
        "job_id": job_id,
        "status": JobStatus.QUEUED.value,
        "progress": 0.0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "result_url": None,
        "error": None,
    }

    await r.set(f"job:{job_id}", json.dumps(job_data), ex=JOB_TTL)
    await r.close()
    return job_id


async def get_job(job_id: str) -> Optional[JobInfo]:
    """Read current job state from Redis."""
    r = await _get_redis()
    raw = await r.get(f"job:{job_id}")
    await r.close()

    if not raw:
        return None

    data = json.loads(raw)
    return JobInfo(**data)


async def update_job(
    job_id: str,
    *,
    status: Optional[JobStatus] = None,
    progress: Optional[float] = None,
    result_url: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Partially update a job's state in Redis."""
    r = await _get_redis()
    raw = await r.get(f"job:{job_id}")

    if not raw:
        await r.close()
        return

    data = json.loads(raw)

    if status is not None:
        data["status"] = status.value
    if progress is not None:
        data["progress"] = progress
    if result_url is not None:
        data["result_url"] = result_url
    if error is not None:
        data["error"] = error

    await r.set(f"job:{job_id}", json.dumps(data), ex=JOB_TTL)
    await r.close()
