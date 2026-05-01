# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""EXIF parsing endpoint — sync (small payload, fast response).

POST /api/exif/parse — accepts uploaded JPEG files, returns parsed EXIF data.
Frontend uses exifr browser-side by default; this is the server-side fallback
for custom firmware EXIF tags.

When Google Drive upload is enabled, images are also persisted to Supabase
Storage and an async ARQ job is enqueued to copy them to Drive.

Supports folder uploads: if the frontend sends a ``paths`` form field with
relative paths (e.g. ``MEDIA/655BC4E5/IMAGES.000/9DB650A0.JPG``), the router
extracts the 8-character deployment-ID prefix from the folder hierarchy and
matches it against Supabase deployments.
"""

import asyncio
import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import structlog
from fastapi import APIRouter, File, Form, Header, Request, UploadFile
from fastapi.responses import JSONResponse

from app.config import settings
from app.dependencies import get_optional_user
from app.domain.exif import parse_exif_from_bytes
from app.jobs.definitions import upload_drive_images_job
from app.jobs.runner import enqueue_local_job
from app.jobs.store import create_job
from app.schemas.common import ApiMeta, ApiResponse
from app.services.azure_storage import store_blob
from app.services.supabase_client import create_service_client

logger = structlog.get_logger()

router = APIRouter(prefix="/api/exif", tags=["exif"])

# Regex to extract the 8-char deployment prefix from the SD card folder path
# e.g.  MEDIA/655BC4E5/IMAGES.000/file.JPG  →  655BC4E5
_FOLDER_DEP_RE = re.compile(r"MEDIA[/\\]([A-Fa-f0-9]{8})[/\\]", re.IGNORECASE)


def _hex_filename_to_timestamp(filename: str) -> Optional[str]:
    """Decode a firmware hex filename (e.g. 9DB650A0.JPG) into an ISO timestamp.

    The firmware encodes timestamps as ``(unix_seconds << 4) + sub_second``
    in an 8-character hex string.  Shifting right by 4 recovers the second.
    """
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    try:
        value = int(stem, 16)
        seconds = value >> 4
        if seconds < 946684800:  # before year 2000 — probably not a real timestamp
            return None
        return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y:%m:%d %H:%M:%S")
    except (ValueError, OSError):
        return None


@router.post("/parse")
async def parse_exif(
    request: Request,
    files: List[UploadFile] = File(...),
    paths: List[str] = Form([]),
    project_id: Optional[str] = Form(None),
    deployment_id: Optional[str] = Form(None),
    upload_to_drive: Optional[bool] = Form(False),
    authorization: Optional[str] = Header(None),
):
    """Parse EXIF metadata from one or more uploaded JPEG files.

    Optionally uploads images to Supabase Storage and enqueues an
    async Google Drive upload job.

    Form fields
    -----------
    files : List[UploadFile]
        JPEG image files to analyse.
    paths : List[str], optional
        Relative paths from folder upload (e.g. ``MEDIA/655BC4E5/IMAGES.000/file.JPG``).
        Used to extract deployment IDs from the SD card folder hierarchy.
    project_id : str, optional
        User-selected project ID for Drive folder organisation.
    deployment_id : str, optional
        User-selected deployment ID for Drive subfolder organisation.
    upload_to_drive : bool, optional
        Whether to enqueue a Drive upload job (default ``False``).
    """
    results = []
    file_contents: List[bytes] = []

    # ── 0. Enforce image limit for unauthenticated users ─────────
    MAX_ANON_IMAGES = 50
    user = await get_optional_user(authorization)
    if not user and len(files) > MAX_ANON_IMAGES:
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "IMAGE_LIMIT_EXCEEDED",
                    "message": (
                        f"Unauthenticated users can analyse up to {MAX_ANON_IMAGES} images. "
                        f"You uploaded {len(files)}. Please log in to remove this limit."
                    ),
                }
            },
        )

    # ── 1. Parse EXIF from each file ─────────────────────────────
    for i, upload in enumerate(files):
        content = await upload.read()
        file_contents.append(content)
        parsed = parse_exif_from_bytes(content)

        # Enrich with folder-path deployment ID if available
        rel_path = paths[i] if i < len(paths) else None
        folder_dep_id = None
        if rel_path:
            m = _FOLDER_DEP_RE.search(rel_path)
            if m:
                folder_dep_id = m.group(1).upper()

        # Priority: folder path → EXIF tag
        effective_dep_id = folder_dep_id or parsed.get("deployment_id")
        if effective_dep_id and not parsed.get("deployment_id"):
            parsed["deployment_id"] = effective_dep_id
        if folder_dep_id:
            parsed["deployment_id_source"] = "folder_path"

        # Decode hex filename to timestamp if EXIF datetime is missing
        if not parsed.get("date") and upload.filename:
            hex_ts = _hex_filename_to_timestamp(upload.filename)
            if hex_ts:
                parsed["date"] = hex_ts
                parsed["date_source"] = "hex_filename"

        results.append(
            {
                "filename": upload.filename,
                "relative_path": rel_path,
                "exif": parsed,
            }
        )

    # ── 2. Determine if any deployments exist ───────────────────────
    # Collect both full UUIDs and 8-char prefixes from folder paths
    deployment_ids: set[str] = set()
    folder_prefixes: set[str] = set()
    for res in results:
        dep_id = res.get("exif", {}).get("deployment_id")
        if dep_id:
            if len(dep_id) == 8:  # 8-char folder prefix
                folder_prefixes.add(dep_id.lower())
            else:
                deployment_ids.add(dep_id)

    # ── 3. Drive upload pipeline ─────────────────────────────────
    # user is already resolved from step 0 (image limit check)

    if not settings.GOOGLE_DRIVE_ENABLED:
        drive_upload_info = {"enabled": False, "reason": "server_disabled"}
    elif not upload_to_drive:
        drive_upload_info = {"enabled": False, "reason": "not_requested"}
    elif not user:
        drive_upload_info = {"enabled": True, "status": "error", "error": "Authentication required to upload images to Google Drive. Please log in."}
    elif not deployment_ids and not folder_prefixes:
        drive_upload_info = {"enabled": True, "status": "skipped", "reason": "no_deployment_id"}
    else:
        drive_upload_info = None  # will be set below

    drive_enabled = drive_upload_info is None

    if drive_enabled:
        try:
            drive_upload_info = await _enqueue_drive_upload(
                request=request,
                files=files,
                file_contents=file_contents,
                results=results,
                deployment_ids=list(deployment_ids),
                folder_prefixes=list(folder_prefixes),
            )
        except Exception as exc:
            logger.error("drive_enqueue_failed", error=str(exc))
            drive_upload_info = {
                "enabled": True,
                "status": "error",
                "error": str(exc),
            }

    return ApiResponse(
        data={
            "images": results,
            "drive_upload": drive_upload_info,
        },
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None) if request else None),
    )


async def _enqueue_drive_upload(
    *,
    request: Request,
    files: List[UploadFile],
    file_contents: List[bytes],
    results: list,
    deployment_ids: List[str],
    folder_prefixes: Optional[List[str]] = None,
) -> dict:
    """Upload images to Supabase Storage and enqueue the Drive upload job.

    Returns a dict describing the enqueued job for the API response.
    """

    client = create_service_client()
    context_map = {}
    prefix_to_full_id = {}  # maps 8-char prefix → full UUID

    # ── Look up project names from Supabase ───────────────────────
    # 1. Exact ID lookup for full UUIDs
    if deployment_ids:
        try:
            dep_resp = (
                client.table("deployments")
                .select("id, deployment_start, deployment_end, location_name, latitude, longitude, project_id, projects(id, name)")
                .in_("id", deployment_ids)
                .execute()
            )
            for dep_row in dep_resp.data:
                dep_id = dep_row["id"]
                dep_start = dep_row.get("deployment_start")
                dep_date = dep_start[:10] if dep_start else datetime.now(timezone.utc).strftime("%Y-%m-%d")
                deployment_info = {
                    "id": dep_id,
                    "date": dep_date,
                    "deployment_start": dep_start,
                    "deployment_end": dep_row.get("deployment_end"),
                    "location_name": dep_row.get("location_name", ""),
                    "latitude": dep_row.get("latitude"),
                    "longitude": dep_row.get("longitude"),
                }
                project_info = None
                proj = dep_row.get("projects")
                if proj:
                    project_info = {"id": proj["id"], "name": proj["name"]}

                context_map[dep_id] = {
                    "deployment": deployment_info,
                    "project": project_info,
                }
        except Exception as exc:
            logger.warning("deployment_batch_lookup_failed", error=str(exc))

    # 2. Prefix lookup for 8-char folder-derived IDs
    if folder_prefixes:
        for prefix in folder_prefixes:
            try:
                prefix_resp = (
                    client.table("deployments")
                    .select("id, deployment_start, deployment_end, location_name, latitude, longitude, project_id, projects(id, name)")
                    .ilike("id", f"{prefix}%")
                    .limit(1)
                    .execute()
                )
                if prefix_resp.data:
                    dep_row = prefix_resp.data[0]
                    dep_id = dep_row["id"]
                    dep_start = dep_row.get("deployment_start")
                    dep_date = dep_start[:10] if dep_start else datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    deployment_info = {
                        "id": dep_id,
                        "date": dep_date,
                        "deployment_start": dep_start,
                        "deployment_end": dep_row.get("deployment_end"),
                        "location_name": dep_row.get("location_name", ""),
                        "latitude": dep_row.get("latitude"),
                        "longitude": dep_row.get("longitude"),
                    }
                    project_info = None
                    proj = dep_row.get("projects")
                    if proj:
                        project_info = {"id": proj["id"], "name": proj["name"]}
                    context_map[dep_id] = {
                        "deployment": deployment_info,
                        "project": project_info,
                    }
                    prefix_to_full_id[prefix.upper()] = dep_id
                    logger.info("folder_prefix_resolved", prefix=prefix, full_id=dep_id)
                else:
                    logger.warning("folder_prefix_not_found", prefix=prefix)
            except Exception as exc:
                logger.warning("folder_prefix_lookup_failed", prefix=prefix, error=str(exc))

    # ── Upload files to Supabase Storage ─────────────────────────
    max_size = settings.GOOGLE_DRIVE_MAX_FILE_SIZE_MB * 1024 * 1024
    storage_entries = []

    sem = asyncio.Semaphore(10)

    async def _process_file(i: int, upload: UploadFile, content: bytes):
        if len(content) > max_size:
            logger.warning(
                "file_too_large_for_drive",
                filename=upload.filename,
                size_bytes=len(content),
            )
            return None

        exif_data = results[i].get("exif", {}) if i < len(results) else {}
        file_dep_id = exif_data.get("deployment_id")

        # Resolve 8-char folder prefix to full UUID if available
        if file_dep_id and len(file_dep_id) == 8:
            resolved = prefix_to_full_id.get(file_dep_id.upper())
            if resolved:
                file_dep_id = resolved

        if not file_dep_id:
            logger.info("file_skipped_no_deployment_id", filename=upload.filename)
            return None

        file_context = context_map.get(file_dep_id)
        if not file_context:
            exif_date = exif_data.get("date", "")
            dep_date = exif_date[:10] if exif_date else datetime.now(timezone.utc).strftime("%Y-%m-%d")
            file_context = {
                "deployment": {"id": file_dep_id, "date": dep_date},
                "project": None,
            }

        # Buffer to local disk instead of Supabase

        blob_id = str(uuid.uuid4())

        async with sem:
            await store_blob(blob_id, content, metadata={})
            uploaded = True

        return {
            "blob_id": blob_id,
            "filename": upload.filename,
            "timestamp": exif_data.get("date"),
            "project": file_context["project"],
            "deployment": file_context["deployment"],
            "newly_uploaded": uploaded,
        }

    tasks = [_process_file(i, upload, content) for i, (upload, content) in enumerate(zip(files, file_contents))]
    results_list = await asyncio.gather(*tasks)

    for res in results_list:
        if res:
            storage_entries.append(res)

    if not storage_entries:
        return {"enabled": True, "status": "skipped", "reason": "no_files_stored"}

    # ── Enqueue ARQ job ──────────────────────────────────────────
    job_id = await create_job()

    payload = {
        "files": storage_entries,
    }

    try:
        enqueue_local_job(upload_drive_images_job(job_id, payload))
    except Exception as exc:
        logger.error("arq_enqueue_failed", job_id=job_id, error=str(exc))
        return {
            "enabled": True,
            "status": "error",
            "error": "Failed to queue asynchronous upload job",
        }

    dup_count = sum(1 for e in storage_entries if not e.get("newly_uploaded"))

    return {
        "enabled": True,
        "job_id": job_id,
        "status": "queued",
        "file_count": len(storage_entries),
        "duplicates_skipped": dup_count,
    }
