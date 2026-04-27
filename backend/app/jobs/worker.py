# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""ARQ worker settings.

Run the worker with: ``arq app.jobs.worker.WorkerSettings``

NOTE: This module requires Redis + ARQ to be installed and configured.
Currently, the system uses in-process asyncio tasks (see runner.py).
This file exists as the target architecture for the Redis+ARQ migration.
"""

try:
    from arq.connections import RedisSettings

    from app.config import settings
    from app.jobs.definitions import JOBS


    class WorkerSettings:
        """ARQ worker configuration."""

        functions = JOBS
        redis_settings = RedisSettings.from_dsn(settings.REDIS_URL) if settings.REDIS_URL else None

        # Worker tuning
        max_jobs = 10
        job_timeout = 300  # 5 minutes max per job
        health_check_interval = 30

except ImportError:
    # ARQ is not installed — this is expected in the current architecture.
    # The system uses runner.py (in-process asyncio) instead.
    WorkerSettings = None  # type: ignore[assignment,misc]
