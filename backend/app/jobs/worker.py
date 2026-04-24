# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""ARQ worker settings.

Run the worker with: ``arq app.jobs.worker.WorkerSettings``
"""

from arq.connections import RedisSettings

from app.config import settings
from app.jobs.definitions import JOBS


class WorkerSettings:
    """ARQ worker configuration."""

    functions = JOBS
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)

    # Worker tuning
    max_jobs = 10
    job_timeout = 300  # 5 minutes max per job
    health_check_interval = 30
