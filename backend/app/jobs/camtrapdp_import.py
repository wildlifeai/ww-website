# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The CamtrapDP import job (#454): the work ``POST /api/camtrapdp/import`` used to do in the request.

The route keeps the package in the object store (or checks the key the browser uploaded it
under, on S3) and starts ``import_camtrapdp_job``, which runs in the API process on Azure
(``IN_PROCESS_JOBS``) and on the ``cpu`` function on AWS. The steps, each reported on the job:
read and parse the package, register missing taxa from iNaturalist,
``domain.camtrapdp.import_package``, archive the images inside the ZIP and point their ``media``
rows at the copies, then the optional AI job. The finished job carries the import's counts and
warnings in ``result``; the package is deleted either way.

A failure is written to the job, never raised: a retried invoke would create the project again.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

import structlog

from app.domain import camtrapdp as domain
from app.jobs.store import create_job, job_heartbeat, update_job
from app.schemas.camtrapdp import CamtrapImportResult
from app.schemas.job import JobStatus
from app.services import object_store
from app.services.db_utils import rows_of
from app.services.supabase_client import create_service_client

logger = structlog.get_logger()

# Warnings kept on the finished job, which the website polls every 2 s; the rest are counted.
MAX_RESULT_WARNINGS = 100
# Seconds between two progress writes inside a step.
REPORT_EVERY_SECONDS = 2.0

Report = Callable[[float, str], Awaitable[None]]


class ImportFailed(Exception):
    """A package the import cannot use. The message is shown to the user."""


def _reporter(job_id: str) -> Report:
    """Progress for one job, written at most every :data:`REPORT_EVERY_SECONDS`."""
    last = 0.0

    async def report(progress: float, message: str) -> None:
        nonlocal last
        if time.monotonic() - last < REPORT_EVERY_SECONDS:
            return
        last = time.monotonic()
        await update_job(job_id, progress=progress, message=message)

    return report


async def _read_package(package_key: str) -> domain.CamtrapPackage:
    found = await object_store.get_object(package_key)
    if not found:
        raise ImportFailed("The uploaded package is gone. Send it again.")
    data = found[0]
    if len(data) > domain.MAX_UPLOAD_BYTES:
        raise ImportFailed(f"File exceeds the {domain.MAX_UPLOAD_BYTES // 1_048_576} MB limit.")
    try:
        pkg = await asyncio.to_thread(domain.parse_zip, data)
    except ValueError as exc:
        raise ImportFailed(str(exc)) from exc
    pkg.warnings.extend(domain.validate_package(pkg))
    return pkg


async def _register_missing_taxa(svc: Any, pkg: domain.CamtrapPackage, report: Report) -> None:
    """Add each scientific name the ``taxa`` table lacks, from iNaturalist; a miss is a warning."""
    unique_names = {obs.get("scientificName", "").strip() for obs in pkg.observations if obs.get("scientificName", "").strip()}
    if not unique_names:
        return
    try:
        existing = await asyncio.to_thread(lambda: svc.table("taxa").select("scientific_name").in_("scientific_name", list(unique_names)).execute())
        missing = sorted(unique_names - {row["scientific_name"] for row in rows_of(existing)})

        from app.domain.inaturalist import search_and_fetch_inat_taxon

        for n, name in enumerate(missing):
            await report(0.05 + 0.2 * n / len(missing), f"Looking up species {n + 1} of {len(missing)} on iNaturalist…")
            try:
                new_taxon = await search_and_fetch_inat_taxon(name)
                if not new_taxon:
                    logger.warning("camtrapdp_import_taxon_not_found_on_inat", name=name)
                    pkg.warnings.append(f"Could not find a valid iNaturalist taxon for '{name}'.")
                    continue
                resolved_name = new_taxon.get("scientific_name", "")
                # iNat may resolve a synonym to an accepted name the table already has
                # (Anas strepera to Mareca strepera): check again, or the insert is a duplicate key.
                resolved = await asyncio.to_thread(
                    lambda rn=resolved_name: svc.table("taxa").select("scientific_name").eq("scientific_name", rn).limit(1).execute()
                )
                if rows_of(resolved):
                    logger.info("camtrapdp_import_taxon_already_exists_as_synonym", queried=name, resolved=resolved_name)
                    continue
                await asyncio.to_thread(lambda t=new_taxon: svc.table("taxa").insert(t).execute())
                logger.info("camtrapdp_import_registered_taxon", name=name, resolved=resolved_name, inat_id=new_taxon.get("inat_taxon_id"))
            except Exception as e:
                logger.error("camtrapdp_import_taxon_insert_failed", name=name, error=str(e))
                pkg.warnings.append(f"Failed to register taxon '{name}': {e}")
    except Exception as e:
        logger.error("camtrapdp_import_taxa_check_failed", error=str(e))
        pkg.warnings.append(f"Failed to verify existing taxa: {e}")


async def _archive_zip_images(svc: Any, result: CamtrapImportResult, report: Report) -> None:
    """Archive the images that were inside the ZIP and point their ``media`` rows at the copies.

    Rows whose ``filePath`` is a public URL keep it. Without this patch a ZIP image keeps its
    relative path and the resolver cannot serve it (blank thumbnails).
    """
    pending = result.pending_drive_uploads
    if not pending:
        return
    from app.config import settings

    if not settings.archive_enabled:
        result.warnings.append(
            f"{len(pending)} image(s) found in zip but there is no archive to store them in "
            "(GOOGLE_DRIVE_ENABLED, or S3_BUCKET with ARCHIVE_BACKEND=s3). Files stored with their original relative paths."
        )
        return
    try:
        from app.services.google_drive import build_deployment_folder_name, sanitize_filename, slugify
        from app.services.object_store import archive_service, archived_path

        files = [
            {
                "file_bytes": p.file_bytes,
                "filename": p.filename,
                "media_id": p.media_id,
                "timestamp": p.deployment_start,
                "mime_type": p.mime_type,
                "project": {"id": p.project_id, "name": p.project_name},
                "deployment": {
                    "id": p.deployment_id,
                    "deployment_start": p.deployment_start,
                    "deployment_end": p.deployment_end,
                    "location_name": p.location_name or "",
                },
                "_project_folder": f"{slugify(p.project_name)}_{p.project_id[:8]}",
                "_deployment_folder": build_deployment_folder_name(p.deployment_start, p.deployment_end, p.deployment_id),
                "drive_filename": sanitize_filename(p.deployment_start, p.filename),
            }
            for p in pending
        ]

        async def on_file(index: int = 0, total: int = 0, **_kw: Any) -> None:
            if index and total:
                await report(0.5 + 0.35 * index / total, f"Archived {index} of {total} photos…")

        stats = await archive_service().upload_analysis_images(files, file_callback=on_file)
        uploaded = [f for f in stats.pop("files", []) if f.get("media_id")]
        patched = 0
        for n, uf in enumerate(uploaded):
            try:
                await asyncio.to_thread(
                    lambda u=uf: svc.table("media").update({"file_path": archived_path(u), "file_public": False}).eq("id", u["media_id"]).execute()
                )
                patched += 1
            except Exception as exc:
                logger.warning("camtrapdp_media_path_patch_failed", media_id=uf.get("media_id"), error=str(exc))
            await report(0.85 + 0.1 * (n + 1) / len(uploaded), f"Linked {n + 1} of {len(uploaded)} photos to their archived copies…")
        if patched:
            logger.info("camtrapdp_media_paths_patched", count=patched)
        result.drive_uploads = stats
        logger.info("camtrapdp_drive_upload_complete", **stats)
    except Exception as exc:
        logger.error("camtrapdp_drive_upload_failed", error=str(exc))
        result.warnings.append(f"Archiving the images in the package failed: {exc}")


async def _start_ai(result: CamtrapImportResult, user_id: str) -> None:
    """SpeciesNet and the Wildlife Brain over the media whose files were in the ZIP (opt-in).

    Not the fileless CSV references: media prep cannot resolve them, and hundreds of them once
    blocked the event loop. ``force``: imported labels must not make the pipeline skip these
    media. A no-op unless ``FF_ML_ENABLED``.
    """
    from app.config import settings

    pending = result.pending_drive_uploads
    dep_ids = sorted({p.deployment_id for p in pending if p.deployment_id})
    media_ids = sorted({p.media_id for p in pending if p.media_id})
    if not settings.FF_ML_ENABLED or not dep_ids:
        return
    from app.jobs.dispatch import enqueue_job

    try:
        label = f"AI analysis, {len(media_ids)} imported image(s)"
        ai_job_id = await create_job(user_id=user_id, kind="ai_pipeline", label=label, deployment_ids=dep_ids)
        await enqueue_job("annotate_deployments_job", ai_job_id, dep_ids, user_id, True, media_ids)
    except Exception as exc:
        logger.error("camtrapdp_ai_enqueue_failed", error=str(exc))
        result.warnings.append(f"AI analysis could not be started: {exc}")
        return
    result.ai_job_id = ai_job_id
    logger.info("camtrapdp_ai_enqueued", job_id=ai_job_id, deployments=len(dep_ids))


async def _import(job_id: str, package_key: str, filename: str, user_id: str, org_id: str, annotation_mode: str, run_ai: bool) -> CamtrapImportResult:
    report = _reporter(job_id)
    pkg = await _read_package(package_key)
    logger.info(
        "camtrapdp_import_started",
        job_id=job_id,
        user_id=user_id,
        org_id=org_id,
        filename=filename,
        deployments=len(pkg.deployments),
        media=len(pkg.media),
        observations=len(pkg.observations),
    )
    svc = create_service_client()
    await update_job(job_id, progress=0.05, message="Checking the species against iNaturalist…")
    await _register_missing_taxa(svc, pkg, report)

    await update_job(
        job_id,
        progress=0.25,
        message=f"Importing {len(pkg.deployments)} deployments, {len(pkg.media)} photos and {len(pkg.observations)} observations…",
    )
    result = await asyncio.to_thread(domain.import_package, pkg, user_id, org_id, svc, None, annotation_mode)

    if result.pending_drive_uploads:
        await update_job(job_id, progress=0.5, message=f"Archiving {len(result.pending_drive_uploads)} photos from the package…")
        await _archive_zip_images(svc, result, report)
        if run_ai:
            await _start_ai(result, user_id)
    result.pending_drive_uploads = []  # the image bytes stay out of the job row
    return result


def result_of(result: CamtrapImportResult) -> dict:
    """What the finished job carries: the counts, the project, and at most :data:`MAX_RESULT_WARNINGS` warnings."""
    out = result.model_dump(exclude={"pending_drive_uploads"})
    extra = len(result.warnings) - MAX_RESULT_WARNINGS
    if extra > 0:
        out["warnings"] = result.warnings[:MAX_RESULT_WARNINGS] + [f"... and {extra} more warning(s)."]
    return out


async def run_import(
    job_id: str, package_key: str, filename: str, user_id: str, org_id: str, annotation_mode: str = "final", run_ai: bool = False
) -> None:
    """Import the package at ``package_key`` into a new project of ``org_id``, owned by ``user_id``."""
    await update_job(job_id, status=JobStatus.PROCESSING, progress=0.02, message="Reading the package…")
    try:
        async with job_heartbeat(job_id):
            result = await _import(job_id, package_key, filename, user_id, org_id, annotation_mode, run_ai)
    except ImportFailed as exc:
        await update_job(job_id, status=JobStatus.FAILED, error=str(exc))
        logger.warning("job_failed", job_type="camtrapdp_import", job_id=job_id, error=str(exc))
        return
    except Exception as exc:
        await update_job(job_id, status=JobStatus.FAILED, error=f"Import failed: {exc}")
        logger.error("job_failed", job_type="camtrapdp_import", job_id=job_id, error=str(exc))
        return
    finally:
        try:
            await object_store.delete_object(package_key)
        except Exception as exc:  # noqa: BLE001 - best-effort; in S3, incoming/ expires after a day anyway
            logger.warning("camtrapdp_package_delete_failed", key=package_key, error=str(exc))

    message = (
        f"Imported {result.deployments_imported} deployments, {result.media_imported} photos and "
        f"{result.observations_imported} observations into '{result.project_name}'"
    )
    if result.warnings:
        message += f", with {len(result.warnings)} warning(s)"
    await update_job(
        job_id,
        status=JobStatus.COMPLETED_WITH_ERRORS if result.warnings else JobStatus.COMPLETED,
        progress=1.0,
        message=message + ".",
        result=result_of(result),
    )
    logger.info("job_complete", job_type="camtrapdp_import", job_id=job_id, project_id=result.project_id, warnings=len(result.warnings))
