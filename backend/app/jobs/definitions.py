# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""ARQ job function definitions.

Each function here is executed by the worker process, not the API server.
They delegate to domain layer classes for actual business logic.
"""

from arq import func

from app.schemas.job import JobStatus
from app.jobs.store import update_job

import structlog

logger = structlog.get_logger()


async def convert_model_job(ctx, job_id: str, file_path: str, user_id: str, org_id: str):
    """Long-running model conversion. Executed by the worker, not the API process."""
    logger.info("job_start", job_type="convert_model", job_id=job_id)
    await update_job(job_id, status=JobStatus.PROCESSING, progress=0.1)

    try:
        # TODO: Wire up ModelDomain.convert_and_upload() when ported
        # from app.domain.model import ModelDomain
        # domain = ModelDomain()
        # result = await domain.convert_and_upload(file_path, user_id, org_id)

        await update_job(job_id, status=JobStatus.COMPLETED, progress=1.0)
        logger.info("job_complete", job_type="convert_model", job_id=job_id)
    except Exception as e:
        await update_job(job_id, status=JobStatus.FAILED, error=str(e))
        logger.error("job_failed", job_type="convert_model", job_id=job_id, error=str(e))
        raise


async def generate_manifest_job(ctx, job_id: str, params: dict):
    """Assemble MANIFEST.zip. May take 10-30s depending on downloads."""
    logger.info("job_start", job_type="generate_manifest", job_id=job_id)
    await update_job(job_id, status=JobStatus.PROCESSING, progress=0.1)

    try:
        # TODO: Wire up ManifestDomain.generate() when ported
        # from app.domain.manifest import ManifestDomain
        # domain = ManifestDomain()
        # result_path = await domain.generate(params)

        await update_job(job_id, status=JobStatus.COMPLETED, progress=1.0)
        logger.info("job_complete", job_type="generate_manifest", job_id=job_id)
    except Exception as e:
        await update_job(job_id, status=JobStatus.FAILED, error=str(e))
        logger.error("job_failed", job_type="generate_manifest", job_id=job_id, error=str(e))
        raise


# Register jobs for ARQ worker discovery
JOBS = [
    func(convert_model_job, name="convert_model"),
    func(generate_manifest_job, name="generate_manifest"),
]
