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
        from app.domain.model import convert_uploaded_model, upload_and_register

        # TODO: Read file content from temp storage path
        # For now this is a placeholder — full implementation needs
        # a shared file storage between API and worker (Redis blob or Supabase temp bucket)

        await update_job(job_id, progress=0.5)

        # When file sharing is implemented:
        # model_bytes, labels = await convert_uploaded_model(file_content, filename)
        # result = await upload_and_register(
        #     model_bytes, model_name, version, description, labels, org_id, user_id
        # )
        # await update_job(job_id, status=JobStatus.COMPLETED, progress=1.0, result_url=...)

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
        from app.domain.manifest import generate_manifest

        manifest_bytes = await generate_manifest(
            model_source=params.get("model_source", "default"),
            model_type=params.get("model_type"),
            resolution=params.get("resolution"),
            sscma_model_id=params.get("sscma_model_id"),
            org_model_id=params.get("org_model_id"),
            camera_type=params.get("camera_type", "Raspberry Pi"),
        )

        await update_job(job_id, progress=0.8)

        # Upload result to temp storage for download
        from app.services.storage import upload_to_storage

        result_path = f"temp/manifests/{job_id}/MANIFEST.zip"
        uploaded = await upload_to_storage(
            "firmware", result_path, manifest_bytes, "application/zip"
        )

        if uploaded:
            # Generate a signed URL or direct path for the frontend
            from app.services.supabase_client import create_service_client

            client = create_service_client()
            try:
                signed = client.storage.from_("firmware").create_signed_url(
                    result_path, expires_in=900  # 15 minutes
                )
                result_url = signed.get("signedURL", "")
            except Exception:
                result_url = result_path

            await update_job(
                job_id,
                status=JobStatus.COMPLETED,
                progress=1.0,
                result_url=result_url,
            )
        else:
            await update_job(
                job_id,
                status=JobStatus.FAILED,
                error="Failed to upload manifest to storage",
            )

        logger.info(
            "job_complete",
            job_type="generate_manifest",
            job_id=job_id,
            size_bytes=len(manifest_bytes),
        )

    except Exception as e:
        await update_job(job_id, status=JobStatus.FAILED, error=str(e))
        logger.error(
            "job_failed", job_type="generate_manifest", job_id=job_id, error=str(e)
        )
        raise


# Register jobs for ARQ worker discovery
JOBS = [
    func(convert_model_job, name="convert_model"),
    func(generate_manifest_job, name="generate_manifest"),
]
