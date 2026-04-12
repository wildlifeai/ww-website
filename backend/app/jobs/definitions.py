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


async def convert_model_job(ctx, job_id: str, user_id: str):
    """Long-running model conversion. Executed by the worker, not the API process.

    Retrieves the uploaded ZIP from Redis blob store, converts via Vela,
    uploads the result to Supabase Storage, and stores a signed URL in the
    job result for the frontend to download.
    """
    logger.info("job_start", job_type="convert_model", job_id=job_id)
    await update_job(job_id, status=JobStatus.PROCESSING, progress=0.1)

    try:
        from app.services.blob_store import retrieve_blob, delete_blob
        from app.domain.model import convert_uploaded_model
        from app.services.storage import upload_to_storage
        from app.services.supabase_client import create_service_client

        # 1. Retrieve uploaded file from Redis
        file_content, metadata = await retrieve_blob(job_id)
        if not file_content:
            raise RuntimeError("Uploaded file not found in blob store (expired?)")

        filename = metadata.get("filename", "model.zip") if metadata else "model.zip"
        await update_job(job_id, progress=0.2)

        # 2. Convert through Vela
        model_bytes, labels = await convert_uploaded_model(file_content, filename)
        await update_job(job_id, progress=0.7)

        # 3. Upload result to temp storage
        result_path = f"temp/conversions/{job_id}/ai_model.zip"
        uploaded = await upload_to_storage(
            "ai-models", result_path, model_bytes, "application/zip"
        )

        if uploaded:
            # Generate a signed download URL (15 min expiry)
            client = create_service_client()
            try:
                signed = client.storage.from_("ai-models").create_signed_url(
                    result_path, expires_in=900
                )
                result_url = signed.get("signedURL", result_path)
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
                job_id, status=JobStatus.FAILED, error="Failed to upload conversion result"
            )

        # 4. Clean up blob from Redis
        await delete_blob(job_id)

        logger.info(
            "job_complete",
            job_type="convert_model",
            job_id=job_id,
            size_bytes=len(model_bytes),
            labels=labels,
        )

    except Exception as e:
        await update_job(job_id, status=JobStatus.FAILED, error=str(e))
        logger.error("job_failed", job_type="convert_model", job_id=job_id, error=str(e))
        # Clean up blob even on failure
        try:
            from app.services.blob_store import delete_blob
            await delete_blob(job_id)
        except Exception:
            pass
        raise


async def generate_manifest_job(ctx, job_id: str, params: dict):
    """Assemble MANIFEST.zip. May take 10-30s depending on downloads."""
    logger.info("job_start", job_type="generate_manifest", job_id=job_id)
    await update_job(job_id, status=JobStatus.PROCESSING, progress=0.1)

    try:
        from app.domain.manifest import generate_manifest
        from app.services.storage import upload_to_storage
        from app.services.supabase_client import create_service_client

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
        result_path = f"temp/manifests/{job_id}/MANIFEST.zip"
        uploaded = await upload_to_storage(
            "firmware", result_path, manifest_bytes, "application/zip"
        )

        if uploaded:
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
