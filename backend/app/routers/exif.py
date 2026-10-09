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

import httpx
import structlog
from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from app.authz import assert_access, classify_deployment_access, deployment_id_prefix_bounds
from app.config import settings
from app.dependencies import get_optional_user, is_email_confirmed
from app.domain.exif import is_test_photo, parse_exif_from_bytes, resolve_deployment_source
from app.jobs.definitions import upload_drive_images_job
from app.jobs.runner import enqueue_local_job
from app.jobs.store import create_job
from app.middleware.rate_limit import limiter
from app.schemas.common import ApiMeta, ApiResponse
from app.services.azure_storage import store_blob
from app.services.supabase_client import create_service_client, reset_service_client

logger = structlog.get_logger()

router = APIRouter(prefix="/api/exif", tags=["exif"])

# Regex to extract the 8-char deployment prefix from the SD card folder path
# e.g.  MEDIA/655BC4E5/IMAGES.000/file.JPG  →  655BC4E5
_FOLDER_DEP_RE = re.compile(r"MEDIA[/\\]([A-Fa-f0-9]{8})[/\\]", re.IGNORECASE)


def _is_bmp(content: bytes) -> bool:
    """True if the bytes are a Windows BMP (magic ``BM``)."""
    return len(content) >= 2 and content[:2] == b"BM"


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


async def _auto_create_deployments(deployment_ids: List[str], project_id: str, user_id: str) -> List[str]:
    """Create any of ``deployment_ids`` that don't exist yet, REUSING the stamped
    UUID, inside ``project_id``. Returns the ids actually created.

    This backs the frontend "create the SD-card deployment" action: the device
    stamps a Deployment_ID that has no matching row, so binding silently drops the
    frames. Reusing the same UUID means every future upload from that device binds
    automatically. Access-guarded (raises 404 if the caller has no role on the
    project — mirrors POST /api/deployments). A placeholder device is created
    because deployments need a non-null device_id + a unique (start, device_id).
    """
    from app.domain.photo_preprocessing import resolve_timezone  # noqa: F401 (parity w/ create_deployment)

    await assert_access(user_id, project_id=project_id)  # 404 unless caller has access

    def _work() -> List[str]:
        svc = create_service_client()
        existing = svc.table("deployments").select("id").in_("id", deployment_ids).execute()
        have = {r["id"] for r in (existing.data or [])}
        to_create = [d for d in deployment_ids if d not in have]
        if not to_create:
            return []

        proj = svc.table("projects").select("id, organisation_id").eq("id", project_id).limit(1).execute()
        if not proj.data:
            raise HTTPException(status_code=404, detail="Project not found")
        org_id = proj.data[0].get("organisation_id")

        created: List[str] = []
        for dep_id in to_create:
            device_id = str(uuid.uuid4())
            svc.table("devices").insert(
                {
                    "id": device_id,
                    "bluetooth_id": str(uuid.uuid4()),
                    "name": f"[SD card] {dep_id[:8]}",
                    "organisation_id": org_id,
                    "modified_by": user_id,
                }
            ).execute()
            svc.table("deployments").insert(
                {
                    "id": dep_id,
                    "project_id": project_id,
                    "device_id": device_id,
                    "setup_by": user_id,
                    "name": f"SD card {dep_id[:8]}",
                    "location_name": f"SD card {dep_id[:8]}",
                    "deployment_start": datetime.now(timezone.utc).isoformat(),
                }
            ).execute()
            created.append(dep_id)
        return created

    return await asyncio.to_thread(_work)


@router.post("/parse")
@limiter.limit("30/minute")
async def parse_exif(
    request: Request,
    files: List[UploadFile] = File(...),
    paths: List[str] = Form([]),
    project_id: Optional[str] = Form(None),
    deployment_id: Optional[str] = Form(None),
    assigned_deployment_id: Optional[str] = Form(None),
    upload_to_drive: Optional[bool] = Form(False),
    run_ai: bool = Form(True),
    auto_create_deployment: bool = Form(False),
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
    run_ai : bool, optional
        Whether to run the AI pipeline + Wildlife Brain after upload (default ``True``).
        ``False`` uploads/archives only — no SpeciesNet/embedding.
    """
    results = []
    file_contents: List[bytes] = []
    test_photos_skipped = 0

    # ── 0. Per-request image cap (anti-abuse) ────────────────────
    # Anon: small cap (also drive a login). Authenticated: a generous per-request
    # ceiling so one call can't enqueue an unbounded batch — the frontend uploads
    # in chunks of ~10, so this only trips on scripted abuse. Cumulative per-org
    # storage quota is enforced separately (see the abuse-prevention plan).
    MAX_ANON_IMAGES = 50
    MAX_AUTH_IMAGES = settings.MAX_UPLOAD_IMAGES_PER_REQUEST
    user = await get_optional_user(authorization)

    # Block storage-consuming uploads for authenticated-but-unverified accounts
    # (anonymous EXIF-only parsing without Drive is still allowed up to the cap).
    if user and upload_to_drive and not is_email_confirmed(user):
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "EMAIL_NOT_CONFIRMED",
                    "message": "Please confirm your email address before uploading images.",
                }
            },
        )

    cap = MAX_ANON_IMAGES if not user else MAX_AUTH_IMAGES
    if len(files) > cap:
        who = "Unauthenticated users" if not user else "Each upload request"
        suffix = " Please log in to raise this limit." if not user else " Split the upload into smaller batches."
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "IMAGE_LIMIT_EXCEEDED",
                    "message": (f"{who} can submit up to {cap} images per request. You sent {len(files)}.{suffix}"),
                }
            },
        )

    # ── 1. Parse EXIF from each file ─────────────────────────────
    for i, upload in enumerate(files):
        content = await upload.read()
        rel_path = paths[i] if i < len(paths) else None
        filename = upload.filename

        # Raw BMP frames carry no EXIF container. When enabled, re-compress them
        # to JPEG in-pipeline (controlled quality, beats the device's) and bind
        # via folder path + hex-filename timestamp like any other frame. When
        # disabled, ignore them (kept in the arrays for index alignment, but with
        # no deployment_id so the Drive step skips them).
        if _is_bmp(content):
            if not settings.FF_BMP_INGEST_ENABLED:
                file_contents.append(content)
                results.append({"filename": filename, "relative_path": rel_path, "exif": {"error": "BMP ingest disabled"}})
                continue
            try:
                from app.services.image_processing import to_jpeg

                content = to_jpeg(content, quality=settings.BMP_JPEG_QUALITY)
                filename = re.sub(r"\.bmp$", ".jpg", filename, flags=re.IGNORECASE) if filename else filename
                parsed: dict = {"deployment_id": None, "converted_from": "bmp"}
            except Exception as exc:
                logger.warning("bmp_convert_failed", filename=upload.filename, error=str(exc))
                file_contents.append(content)
                results.append({"filename": filename, "relative_path": rel_path, "exif": {"error": "BMP conversion failed"}})
                continue
        else:
            parsed = parse_exif_from_bytes(content)
            # A WW500 test photo (no deployment set on the camera) is never uploaded,
            # whatever folder it sits in, so another client cannot store one either.
            # Kept in the arrays for index alignment, flagged and with no deployment,
            # so neither the folder nor an assigned_deployment_id binds it. #287.
            if is_test_photo(parsed):
                test_photos_skipped += 1
                parsed["deployment_id"] = None
                parsed["test_photo"] = True
                file_contents.append(content)
                results.append({"filename": filename, "relative_path": rel_path, "exif": parsed})
                continue

        file_contents.append(content)

        # The card folder holds only an 8-hex prefix of the deployment id; the EXIF
        # tag holds the whole thing and is what the device was actually configured
        # with. EXIF wins; the folder fills in only when there is no tag (a BMP
        # frame, or an image from something other than a WW500). ww-website#140.
        folder_dep_id = None
        if rel_path:
            m = _FOLDER_DEP_RE.search(rel_path)
            if m:
                folder_dep_id = m.group(1).upper()

        dep_id, source = resolve_deployment_source(parsed.get("deployment_id"), folder_dep_id)
        if dep_id:
            parsed["deployment_id"] = dep_id
            parsed["deployment_id_source"] = source

        # Decode hex filename to timestamp if EXIF datetime is missing (the BMP's
        # original .bmp stem still decodes — extension is irrelevant).
        if not parsed.get("date") and upload.filename:
            hex_ts = _hex_filename_to_timestamp(upload.filename)
            if hex_ts:
                parsed["date"] = hex_ts
                parsed["date_source"] = "hex_filename"

        results.append(
            {
                "filename": filename,
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

    # ── 2b. Auto-create missing deployments (reuse the stamped UUID) ──
    # The device stamps a Deployment_ID that has no row yet, so binding drops the
    # frames. When the user opts in (+ picks a project), create those deployments
    # reusing the same UUID so this and every future upload binds.
    auto_created: list[str] = []
    if auto_create_deployment and project_id and user and deployment_ids:
        try:
            auto_created = await _auto_create_deployments(list(deployment_ids), project_id, user.id)
            if auto_created:
                logger.info("auto_created_deployments", ids=auto_created, project_id=project_id)
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("auto_create_deployments_failed", error=str(exc))
            return JSONResponse(
                status_code=400,
                content={"error": {"code": "AUTO_CREATE_FAILED", "message": str(exc)}},
            )

    # ── 3. Drive upload pipeline ─────────────────────────────────
    # user is already resolved from step 0 (image limit check)

    if not settings.GOOGLE_DRIVE_ENABLED:
        drive_upload_info = {"enabled": False, "reason": "server_disabled"}
    elif not upload_to_drive:
        drive_upload_info = {"enabled": False, "reason": "not_requested"}
    elif not user:
        drive_upload_info = {"enabled": True, "status": "error", "error": "Authentication required to upload images to Google Drive. Please log in."}
    elif not deployment_ids and not folder_prefixes and not assigned_deployment_id:
        drive_upload_info = {"enabled": True, "status": "skipped", "reason": "no_deployment_id"}
    else:
        drive_upload_info = None  # will be set below

    drive_enabled = drive_upload_info is None

    if drive_enabled:
        try:
            drive_upload_info = await _enqueue_drive_upload_with_retry(
                request=request,
                files=files,
                file_contents=file_contents,
                results=results,
                deployment_ids=list(deployment_ids),
                folder_prefixes=list(folder_prefixes),
                user_id=user.id if user else None,
                assigned_deployment_id=assigned_deployment_id,
                run_ai=run_ai,
                test_photos_skipped=test_photos_skipped,
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
            "auto_created_deployments": auto_created,
            "test_photos_skipped": test_photos_skipped,
        },
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None) if request else None),
    )


def _is_transient_transport_error(exc: BaseException) -> bool:
    """A dropped or protocol-broken HTTP connection to Supabase, worth one retry.

    The shared service client keeps a single HTTP/2 connection; when the server
    closes it, the request handler and a background Drive job sharing it both
    see ``ConnectionTerminated``. On the 2026-09-05 bench run that cost a whole
    batch its Drive job (and so its media rows) while the response still read
    200 with ``drive_upload.status = "error"``.
    """
    if isinstance(exc, (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError, httpx.ConnectError)):
        return True
    return "ConnectionTerminated" in repr(exc)


async def _enqueue_drive_upload_with_retry(**kwargs) -> dict:
    """Enqueue the Drive job, retrying once with a fresh Supabase client after a
    transient transport error. Re-running is safe: a second Azure buffer copy is
    an orphan blob at worst, and the job is only created on success."""
    try:
        return await _enqueue_drive_upload(**kwargs)
    except Exception as exc:
        if not _is_transient_transport_error(exc):
            raise
        logger.warning("drive_enqueue_retry", error=str(exc))
        reset_service_client()
        return await _enqueue_drive_upload(**kwargs)


async def _enqueue_drive_upload(
    *,
    request: Request,
    files: List[UploadFile],
    file_contents: List[bytes],
    results: list,
    deployment_ids: List[str],
    folder_prefixes: Optional[List[str]] = None,
    user_id: Optional[str] = None,
    assigned_deployment_id: Optional[str] = None,
    run_ai: bool = True,
    test_photos_skipped: int = 0,
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

    # 2. Prefix lookup for 8-char folder-derived IDs. deployments.id is a uuid
    # column, so match a uuid range, not `ilike` (which raises 42883 — that error
    # was swallowed below and silently dropped every BMP frame, which binds by
    # folder prefix rather than EXIF). See deployment_id_prefix_bounds.
    if folder_prefixes:
        for prefix in folder_prefixes:
            bounds = deployment_id_prefix_bounds(prefix)
            if not bounds:
                logger.warning("folder_prefix_invalid", prefix=prefix)
                continue
            lo, hi = bounds
            try:
                prefix_resp = (
                    client.table("deployments")
                    .select("id, deployment_start, deployment_end, location_name, latitude, longitude, project_id, projects(id, name)")
                    .gte("id", lo)
                    .lte("id", hi)
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

    # ── Access enforcement + manual assignment ───────────────────
    # Resolution above uses the service client (bypasses RLS), so enforce access here: images
    # must never attach to a deployment the caller can't reach. Classify every resolved
    # deployment; block the no-access ones, and for unresolved/not-found images fall back to the
    # user's explicit assignment (a deployment they picked or just created in the upload flow).
    blocked_ids: set[str] = set()
    if user_id and context_map:
        access = await classify_deployment_access(user_id, list(context_map.keys()))
        blocked_ids = {dep_id for dep_id, status in access.items() if status == "no_access"}

    valid_assigned_id: Optional[str] = None
    if assigned_deployment_id and user_id:
        assign_status = await classify_deployment_access(user_id, [assigned_deployment_id])
        if assign_status.get(assigned_deployment_id) == "valid":
            valid_assigned_id = assigned_deployment_id
            if assigned_deployment_id not in context_map:
                # Load the assigned deployment's context for Drive folder naming.
                try:
                    a_resp = (
                        client.table("deployments")
                        .select("id, deployment_start, deployment_end, location_name, latitude, longitude, project_id, projects(id, name)")
                        .eq("id", assigned_deployment_id)
                        .limit(1)
                        .execute()
                    )
                    if a_resp.data:
                        dep_row = a_resp.data[0]
                        dep_start = dep_row.get("deployment_start")
                        dep_date = dep_start[:10] if dep_start else datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        proj = dep_row.get("projects")
                        context_map[assigned_deployment_id] = {
                            "deployment": {
                                "id": assigned_deployment_id,
                                "date": dep_date,
                                "deployment_start": dep_start,
                                "deployment_end": dep_row.get("deployment_end"),
                                "location_name": dep_row.get("location_name", ""),
                                "latitude": dep_row.get("latitude"),
                                "longitude": dep_row.get("longitude"),
                            },
                            "project": {"id": proj["id"], "name": proj["name"]} if proj else None,
                        }
                except Exception as exc:
                    logger.warning("assigned_deployment_lookup_failed", error=str(exc))
        else:
            logger.warning("assigned_deployment_not_accessible", deployment_id=assigned_deployment_id)

    blocked_list = sorted(blocked_ids)

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
        # Test photos were flagged in step 1; never store one, not even under an assignment.
        if exif_data.get("test_photo"):
            return None
        # Prefer the post-conversion filename (BMP→.jpg) recorded in results.
        out_filename = (results[i].get("filename") if i < len(results) else None) or upload.filename
        file_dep_id = exif_data.get("deployment_id")

        # Resolve 8-char folder prefix to full UUID (None if it matched no real deployment).
        if file_dep_id and len(file_dep_id) == 8:
            file_dep_id = prefix_to_full_id.get(file_dep_id.upper())

        # Block: the resolved deployment exists but the caller has no access — never attach.
        if file_dep_id and file_dep_id in blocked_ids:
            logger.info("file_blocked_no_access", filename=upload.filename, deployment_id=file_dep_id)
            return None

        # Unresolved (no id, unknown prefix, or not in the DB) → use the caller's explicit
        # assignment when they provided one; otherwise skip (unchanged behaviour).
        if not file_dep_id or file_dep_id not in context_map:
            if valid_assigned_id:
                file_dep_id = valid_assigned_id
            else:
                logger.info("file_skipped_no_deployment_id", filename=upload.filename)
                return None

        file_context = context_map.get(file_dep_id)
        if not file_context:
            return None

        # Buffer to local disk instead of Supabase

        blob_id = str(uuid.uuid4())

        async with sem:
            await store_blob(blob_id, content, metadata={})
            uploaded = True

        return {
            "blob_id": blob_id,
            "filename": out_filename,
            "timestamp": exif_data.get("date"),
            "project": file_context["project"],
            "deployment": file_context["deployment"],
            "newly_uploaded": uploaded,
            # Full parsed EXIF (already JSON-safe) — threaded through the upload
            # job onto media.exif_metadata so camera variant, capture settings
            # and on-device NN scores survive past this request.
            "exif": exif_data or None,
        }

    tasks = [_process_file(i, upload, content) for i, (upload, content) in enumerate(zip(files, file_contents))]
    results_list = await asyncio.gather(*tasks)

    for res in results_list:
        if res:
            storage_entries.append(res)

    if not storage_entries:
        return {
            "enabled": True,
            "status": "skipped",
            "reason": "no_access" if blocked_list else "no_files_stored",
            "blocked_deployments": blocked_list,
        }

    # ── Enqueue ARQ job ──────────────────────────────────────────
    job_id = await create_job(
        user_id=user_id,
        kind="upload",
        label=f"Upload — {len(storage_entries)} file{'s' if len(storage_entries) != 1 else ''}",
    )

    payload = {
        "files": storage_entries,
        "user_id": user_id,
        # Gate the post-upload AI/Brain phase (upload_drive_images_job reads this).
        "run_ai": run_ai,
        # Counted into the job summary; the files themselves never reach the job.
        "test_photos_skipped": test_photos_skipped,
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
        "blocked_deployments": blocked_list,
    }
