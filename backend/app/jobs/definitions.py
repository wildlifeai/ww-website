# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""ARQ job function definitions.

Each function here is executed by the worker process, not the API server.
They delegate to domain layer classes for actual business logic.
"""

import time
from datetime import datetime, timezone

from app.schemas.job import JobStatus, ProgressPhase, EventType, ProgressEvent
from app.jobs.store import (
    update_job,
    emit_event,
    update_summary,
    start_phase,
    complete_phase,
    get_job,
)

import structlog

logger = structlog.get_logger()


async def convert_model_job(job_id: str, user_id: str):
    """Long-running model conversion. Executed by passing into runner.py.

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


async def generate_manifest_job(job_id: str, params: dict):
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


async def export_camtrapdp_job(job_id: str, org_id: str, params: dict):
    """Export deployment data as CamtrapDP package."""
    logger.info("job_start", job_type="export_camtrapdp", job_id=job_id)
    await update_job(job_id, status=JobStatus.PROCESSING, progress=0.1)

    try:
        from app.domain.public_api import generate_camtrapdp_package
        from app.services.storage import upload_to_storage
        from app.services.supabase_client import create_service_client

        package_bytes = await generate_camtrapdp_package(
            org_id=org_id,
            project_id=params.get("project_id"),
            deployment_ids=params.get("deployment_ids"),
            date_from=params.get("date_from"),
            date_to=params.get("date_to"),
            include_observations=params.get("include_observations", True),
        )

        await update_job(job_id, progress=0.8)

        result_path = f"temp/exports/{job_id}/camtrap-dp.zip"
        uploaded = await upload_to_storage(
            "firmware", result_path, package_bytes, "application/zip"
        )

        if uploaded:
            client = create_service_client()
            try:
                signed = client.storage.from_("firmware").create_signed_url(
                    result_path, expires_in=3600  # 1 hour for exports
                )
                result_url = signed.get("signedURL", result_path)
            except Exception:
                result_url = result_path

            await update_job(
                job_id, status=JobStatus.COMPLETED, progress=1.0, result_url=result_url,
            )
        else:
            await update_job(
                job_id, status=JobStatus.FAILED, error="Failed to upload export"
            )

        logger.info("job_complete", job_type="export_camtrapdp", job_id=job_id)

    except Exception as e:
        await update_job(job_id, status=JobStatus.FAILED, error=str(e))
        logger.error("job_failed", job_type="export_camtrapdp", job_id=job_id, error=str(e))
        raise


async def download_pretrained_job(job_id: str, user_id: str, sscma_uuid: str, org_id: str):
    """Download, convert, and register an SSCMA pretrained model."""
    logger.info("job_start", job_type="download_pretrained", job_id=job_id)
    await update_job(job_id, status=JobStatus.PROCESSING, progress=0.1)

    try:
        from app.domain.model import convert_pretrained_model, upload_and_register

        # 1. Download, optionally compile with Vela, package labels
        # Could take 30-60s if Vela is invoked
        model_bytes, labels, metadata = await convert_pretrained_model(sscma_uuid)
        await update_job(job_id, progress=0.6)

        # 2. Upload to storage and register in DB
        db_model = await upload_and_register(
            model_bytes=model_bytes,
            model_name=metadata.get("name", "Unknown SSCMA Model"),
            model_version=metadata.get("version", "1.0.0"),
            description=metadata.get("description", "Imported from Seeed Studio Model Zoo"),
            labels=labels,
            org_id=org_id,
            user_id=user_id,
        )

        await update_job(job_id, status=JobStatus.COMPLETED, progress=1.0)
        logger.info("job_complete", job_type="download_pretrained", job_id=job_id, model_id=db_model["id"])

    except Exception as e:
        await update_job(job_id, status=JobStatus.FAILED, error=str(e))
        logger.error(
            "job_failed", job_type="download_pretrained", job_id=job_id, error=str(e)
        )
        raise

async def upload_drive_images_job(job_id: str, payload: dict):
    """Upload analysed images to Google Drive.

    Emits structured ``ProgressEvent``\s throughout so the frontend can
    render deterministic progress (no string parsing).  Runs through
    three phases: DOWNLOAD → DRIVE_UPLOAD → CLEANUP.  Cleanup must
    finish before the job is marked complete.

    Payload shape::

        {
            "files": [
                {"blob_id": "...", "filename": "...", "timestamp": "..."}
            ],
            "project": {"id": "...", "name": "..."} | None,
            "deployment": {"id": "...", "date": "YYYY-MM-DD"} | None
        }
    """
    import asyncio

    logger.info("job_start", job_type="upload_drive_images", job_id=job_id)

    file_entries = payload.get("files", [])
    total_files = len(file_entries)

    if not file_entries:
        await update_job(
            job_id, status=JobStatus.COMPLETED, progress=1.0,
            message="No files to process.",
        )
        return

    await update_job(job_id, status=JobStatus.PROCESSING, progress=0.05)
    await update_summary(
        job_id, total=total_files,
        started_at=datetime.now(timezone.utc),
    )
    await emit_event(job_id, ProgressEvent(
        type=EventType.JOB_STARTED,
        phase=ProgressPhase.DOWNLOAD,
        total=total_files,
        message=f"🚀 Starting pipeline for {total_files} images",
    ))

    try:
        from app.services.google_drive import GoogleDriveService
        from app.services.blob_store import retrieve_blob, delete_blob

        # Shared mutable state for heartbeat visibility
        last_event_ts = time.monotonic()

        # ── Heartbeat helper (separate async task) ───────────
        async def _heartbeat_loop(phase, get_progress, stop_event):
            """Fires if no event for 10 s — guarantees the UI never stalls."""
            while not stop_event.is_set():
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=10)
                    break  # stop was set
                except asyncio.TimeoutError:
                    nonlocal last_event_ts
                    if time.monotonic() - last_event_ts >= 10:
                        c, t = get_progress()
                        await emit_event(job_id, ProgressEvent(
                            type=EventType.HEARTBEAT,
                            phase=phase,
                            current=c, total=t,
                            message=f"Still working… ({c}/{t})",
                        ))
                        last_event_ts = time.monotonic()

        # ── Phase 1: DOWNLOAD from Supabase ──────────────────
        await start_phase(job_id, ProgressPhase.DOWNLOAD)

        files_with_bytes = []
        download_completed = 0
        sem = asyncio.Semaphore(5)

        async def fetch(idx, entry):
            nonlocal download_completed, last_event_ts
            async with sem:
                content, _ = await retrieve_blob(entry["blob_id"])
                
                download_completed += 1
                progress = 0.05 + (0.35 * (download_completed / total_files))
                await update_job(job_id, progress=min(progress, 0.40))

                if content:
                    await update_summary(job_id, downloaded_inc=1)
                    await emit_event(job_id, ProgressEvent(
                        type=EventType.FILE_SUCCESS,
                        phase=ProgressPhase.DOWNLOAD,
                        current=download_completed,
                        total=total_files,
                        file_index=idx + 1,
                        filename=entry["filename"],
                        message=f"📥 Loaded image {download_completed}/{total_files} from buffer ✓",
                    ))
                else:
                    await update_summary(job_id, failed_inc=1)
                    await emit_event(job_id, ProgressEvent(
                        type=EventType.FILE_FAILURE,
                        phase=ProgressPhase.DOWNLOAD,
                        current=download_completed,
                        total=total_files,
                        file_index=idx + 1,
                        filename=entry["filename"],
                        message=f"⚠️ Image {download_completed}/{total_files} ({entry['filename']}) failed to download",
                    ))

                last_event_ts = time.monotonic()
                return entry, content

        hb_stop = asyncio.Event()
        hb_task = asyncio.create_task(
            _heartbeat_loop(
                ProgressPhase.DOWNLOAD,
                lambda: (download_completed, total_files),
                hb_stop,
            )
        )

        results = await asyncio.gather(
            *[fetch(i, e) for i, e in enumerate(file_entries)]
        )

        hb_stop.set()
        try:
            await hb_task
        except asyncio.CancelledError:
            pass

        for entry, content in results:
            if content:
                files_with_bytes.append({
                    "file_bytes": content,
                    "filename": entry["filename"],
                    "timestamp": entry.get("timestamp"),
                    "project": entry.get("project"),
                    "deployment": entry.get("deployment"),
                })

        await complete_phase(job_id, ProgressPhase.DOWNLOAD)

        if not files_with_bytes:
            logger.warning("drive_upload_no_files_downloaded", job_id=job_id)
            await update_job(
                job_id, status=JobStatus.FAILED,
                error="No files could be downloaded from storage",
                message="Failed: could not download any files from Supabase.",
            )
            return

        # ── Phase 2: DRIVE UPLOAD ────────────────────────────
        await start_phase(job_id, ProgressPhase.DRIVE_UPLOAD)

        drive = GoogleDriveService()
        drive_total = len(files_with_bytes)
        drive_completed = 0
        last_event_ts = time.monotonic()

        async def on_drive_file_event(
            action, *, filename="", folder_name="",
            index=0, total=0, error="",
        ):
            nonlocal drive_completed, last_event_ts
            last_event_ts = time.monotonic()

            if action == "folder_created":
                await emit_event(job_id, ProgressEvent(
                    type=EventType.FOLDER_CREATED,
                    phase=ProgressPhase.DRIVE_UPLOAD,
                    message=f"📁 Created folder \"{folder_name}\" in Google Drive",
                ))
            elif action == "uploaded":
                drive_completed = index
                await update_summary(job_id, uploaded_inc=1)
                progress = 0.40 + (0.50 * (index / total))
                await update_job(job_id, progress=min(progress, 0.90))
                await emit_event(job_id, ProgressEvent(
                    type=EventType.FILE_SUCCESS,
                    phase=ProgressPhase.DRIVE_UPLOAD,
                    current=index, total=total,
                    filename=filename,
                    message=f"☁️ Uploaded {filename} ({index}/{total}) ✓",
                ))
            elif action == "skipped":
                drive_completed = index
                await update_summary(job_id, skipped_inc=1)
                progress = 0.40 + (0.50 * (index / total))
                await update_job(job_id, progress=min(progress, 0.90))
                await emit_event(job_id, ProgressEvent(
                    type=EventType.FILE_SKIP,
                    phase=ProgressPhase.DRIVE_UPLOAD,
                    current=index, total=total,
                    filename=filename,
                    message=f"⏭️ {filename} ({index}/{total}) already exists (skipped)",
                ))
            elif action == "failed":
                drive_completed = index
                await update_summary(job_id, failed_inc=1)
                await emit_event(job_id, ProgressEvent(
                    type=EventType.FILE_FAILURE,
                    phase=ProgressPhase.DRIVE_UPLOAD,
                    current=index, total=total,
                    filename=filename,
                    message=f"⚠️ Failed to upload {filename} ({index}/{total}): {error}",
                ))

        hb_stop2 = asyncio.Event()
        hb_task2 = asyncio.create_task(
            _heartbeat_loop(
                ProgressPhase.DRIVE_UPLOAD,
                lambda: (drive_completed, drive_total),
                hb_stop2,
            )
        )

        stats = await drive.upload_analysis_images(
            files=files_with_bytes,
            file_callback=on_drive_file_event,
        )

        hb_stop2.set()
        try:
            await hb_task2
        except asyncio.CancelledError:
            pass

        await complete_phase(job_id, ProgressPhase.DRIVE_UPLOAD)

        # ── Phase 3: CLEANUP ─────────────────────────────────
        await start_phase(job_id, ProgressPhase.CLEANUP)
        await update_job(job_id, progress=0.92)

        blob_ids = [entry["blob_id"] for entry in file_entries]

        async def _cleanup_blobs():
            nonlocal last_event_ts
            completed = 0
            total = len(blob_ids)
            for bid in blob_ids:
                try:
                    await delete_blob(bid)
                except Exception:
                    pass
                completed += 1
                
                last_event_ts = time.monotonic()
                if completed % max(1, total // 10) == 0 or completed == total:
                    await emit_event(job_id, ProgressEvent(
                        type=EventType.PROGRESS,
                        phase=ProgressPhase.CLEANUP,
                        current=completed, total=total,
                        message=f"🧹 Cleaning up temporary buffers ({completed}/{total})",
                    ))
                    progress = 0.92 + (0.08 * (completed / total))
                    await update_job(job_id, progress=min(progress, 0.99))
            return completed

        deleted = await _cleanup_blobs()
        if deleted:
            logger.info("drive_upload_intermediate_files_cleaned", count=deleted)

        await complete_phase(job_id, ProgressPhase.CLEANUP)

        # ── Final status (cleanup is done) ───────────────────
        job_data = await get_job(job_id)
        summary = job_data.summary if job_data else None
        failed_count = summary.failed if summary else 0
        uploaded_count = summary.uploaded if summary else 0
        skipped_count = summary.skipped if summary else 0

        final_status = (
            JobStatus.COMPLETED_WITH_ERRORS if failed_count > 0
            else JobStatus.COMPLETED
        )

        if final_status == JobStatus.COMPLETED_WITH_ERRORS:
            final_msg = (
                f"⚠️ Completed with issues — "
                f"{uploaded_count} uploaded, {skipped_count} skipped, {failed_count} failed"
            )
        else:
            final_msg = f"✅ Done — {drive_total} images synced to Google Drive"

        await update_job(
            job_id, status=final_status, progress=1.0, message=final_msg,
        )

        logger.info(
            "job_complete",
            job_type="upload_drive_images",
            job_id=job_id,
            **stats,
        )

    except Exception as e:
        await update_job(
            job_id, status=JobStatus.FAILED, error=str(e),
            message=f"❌ Failed to upload to Google Drive: {str(e)}",
        )
        logger.error(
            "job_failed", job_type="upload_drive_images",
            job_id=job_id, error=str(e),
        )
        # Without ARQ, we don't auto-retry.
        return
