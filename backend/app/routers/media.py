# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Media serving endpoints — proxy images from private storage.

GET /api/media/{media_id}/image?size=thumb → low-res thumbnail (grid)
GET /api/media/{media_id}/image?size=full  → full resolution (detail panel)
POST /api/media/{media_id}/observations/{observation_id}/crop → a person's box crop (#449)
"""

import asyncio
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field

from app.authz import WRITE_REFUSED, require_deployment_access, require_media_access, require_media_write, writable_media_ids
from app.config import settings
from app.dependencies import get_current_user, get_user_client, get_verified_user, require_not_demo
from app.domain.media_registry import BoxCropRefused, RenditionUploadError, resolve_url, with_resolved_urls, write_box_crop
from app.domain.media_resolver import MediaRef, serve_media
from app.domain.pipeline import PipelineBusyError
from app.domain.soft_delete import now_iso, restore_media_as_user, soft_delete_media_as_user
from app.jobs.pipeline_run import start_selected_run
from app.schemas.common import ApiError, ApiMeta, ApiResponse
from app.schemas.pipeline import PipelineStepType
from app.services import supabase_client
from app.services.db_utils import row_of, rows_of

logger = structlog.get_logger()

router = APIRouter(prefix="/api/media", tags=["media"])

# Columns the grid + resolver need (media + its 1:1 media_assets renditions).
_REGISTRY_SELECT = "id, file_path, file_name, timestamp, deployment_id, media_assets(thumbnail_url, preview_url, animal_crop_url, storage_provider)"


def _registry_disabled(req_id: str | None) -> ApiResponse:
    return ApiResponse(
        error=ApiError(code="FEATURE_DISABLED", message="Media Registry is disabled (FF_MEDIA_REGISTRY_ENABLED)."),
        meta=ApiMeta(request_id=req_id),
    )


@router.get("/{media_id}/image", dependencies=[Depends(require_media_access)])
async def get_media_image(
    media_id: str,
    size: Literal["thumb", "full"] = Query("thumb", description="Image size: thumb (grid) or full (detail)"),
    user=Depends(get_current_user),
):
    """Serve a media image by resolving its file_path through the provider registry.

    - ``size=thumb``: fast low-res preview for the thumbnail grid
    - ``size=full``: high-res download for the detail panel (on-demand)

    Uses the authenticated user's Supabase client so RLS enforces
    project membership — users can only view media they have access to.
    """
    client = supabase_client.create_anon_client()

    row = row_of(client.table("media").select("deployment_id, file_path").eq("id", media_id).maybe_single().execute())

    if not row:
        raise HTTPException(status_code=404, detail="Media record not found")

    file_path = row.get("file_path", "")

    # On S3 an original in the bucket, or one too large for Lambda's answer, goes to the
    # browser on a short presigned URL, so the API never proxies (or size-limits) the bytes.
    served = await serve_media(file_path, size=size, media=MediaRef(media_id, str(row.get("deployment_id") or "")))
    if served and served.url:
        return RedirectResponse(served.url, status_code=307, headers={"Cache-Control": "private, max-age=60"})
    if not served:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Image not available — the file path cannot be resolved to a hosted image.",
                "file_path": file_path,
                "suggestions": [
                    "Upload the original images via the Upload Data page.",
                    "Update the media record's file_path with a public URL (https://...).",
                    "If stored in Google Drive, prefix with gdrive:// followed by the file ID.",
                ],
            },
        )

    # Thumbnails cache longer (10min), full-res shorter (5min) since they're on-demand
    cache_ttl = 600 if size == "thumb" else 300
    return Response(
        content=served.data,
        media_type=served.content_type,
        headers={"Cache-Control": f"private, max-age={cache_ttl}"},
    )


# ── Media Registry (Phase 6) ─────────────────────────────────────────


@router.get("/{media_id}/resolve", dependencies=[Depends(require_media_access)])
async def resolve_media_url(
    request: Request,
    media_id: str,
    size: Literal["thumbnail", "preview", "original"] = Query("thumbnail"),
    user=Depends(get_current_user),
):
    """Return a URL a plain ``<img>`` can load for a media item, regardless of storage provider.

    ``url`` is null when the photo has no rendition yet and its original is private.
    """
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_MEDIA_REGISTRY_ENABLED:
        return _registry_disabled(req_id)

    client = supabase_client.create_anon_client()
    row = row_of(client.table("media").select(_REGISTRY_SELECT).eq("id", media_id).maybe_single().execute())
    if not row:
        return ApiResponse(error=ApiError(code="NOT_FOUND", message="Media not found"), meta=ApiMeta(request_id=req_id))

    return ApiResponse(
        data={"media_id": media_id, "size": size, "url": resolve_url(row, size)},
        meta=ApiMeta(request_id=req_id),
    )


@router.post(
    "/{media_id}/observations/{observation_id}/crop",
    dependencies=[Depends(require_media_write), Depends(require_not_demo)],
)
async def box_crop(request: Request, media_id: str, observation_id: str, user=Depends(get_current_user)):
    """After a person's box is drawn, moved or removed: write its crop file to ``crop_url``, or clear
    it, and clear the photo's fingerprint for the next Wildlife Brain run (#449). Behind
    ``FF_LABEL_CHECKS_ENABLED``."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_LABEL_CHECKS_ENABLED:
        return ApiResponse(
            error=ApiError(code="FEATURE_DISABLED", message="Box crops are part of label checks (FF_LABEL_CHECKS_ENABLED)."),
            meta=ApiMeta(request_id=req_id),
        )
    try:
        url = await write_box_crop(media_id, observation_id)
    except BoxCropRefused as e:
        return ApiResponse(error=ApiError(code="NOT_FOUND", message=str(e)), meta=ApiMeta(request_id=req_id))
    except RenditionUploadError as e:
        return ApiResponse(error=ApiError(code="CROP_FAILED", message=str(e), retryable=True), meta=ApiMeta(request_id=req_id))
    return ApiResponse(data={"observation_id": observation_id, "crop_url": url}, meta=ApiMeta(request_id=req_id))


@router.get("/registry/{deployment_id}", dependencies=[Depends(require_deployment_access)])
async def media_registry(
    request: Request,
    deployment_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=500),
    user=Depends(get_current_user),
):
    """Paginated media list with pre-resolved URLs, null where only the private original exists."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_MEDIA_REGISTRY_ENABLED:
        return _registry_disabled(req_id)

    offset = (page - 1) * page_size
    client = supabase_client.create_anon_client()
    result = (
        client.table("media")
        .select(_REGISTRY_SELECT)
        .eq("deployment_id", deployment_id)
        .is_("deleted_at", "null")
        .order("timestamp", desc=False)
        .range(offset, offset + page_size - 1)
        .execute()
    )
    rows = [with_resolved_urls(r) for r in rows_of(result)]
    return ApiResponse(
        data={"media": rows, "page": page, "page_size": page_size, "count": len(rows)},
        meta=ApiMeta(request_id=req_id),
    )


@router.post("/thumbnails/{deployment_id}", dependencies=[Depends(require_deployment_access)])
async def enqueue_thumbnail_backfill(
    request: Request,
    deployment_id: str,
    user=Depends(get_current_user),
):
    """Enqueue an async job to generate thumbnails/previews for existing media."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_MEDIA_REGISTRY_ENABLED:
        return _registry_disabled(req_id)

    from app.jobs.definitions import start_thumbnail_backfill

    # The job shows in the user's processing history and drives the grid's "Processing…" for
    # that deployment; a thumbnails job still waiting there (an upload's) is reused.
    job_id = await start_thumbnail_backfill(deployment_id, user.id)
    return ApiResponse(
        data={"job_id": job_id, "status": "queued", "deployment_id": deployment_id},
        meta=ApiMeta(request_id=req_id),
    )


# ── Batch operations ─────────────────────────────────────────────────────────


class BatchDeleteRequest(BaseModel):
    """Soft-delete multiple media records."""

    media_ids: list[str] = Field(..., max_length=500)


@router.delete("/batch", dependencies=[Depends(require_not_demo)])
async def batch_delete_media(
    request: Request,
    body: BatchDeleteRequest,
    user=Depends(get_current_user),
    user_client=Depends(get_user_client),
):
    """Soft-delete multiple media records (sets deleted_at).

    Runs as the requesting user (not the service role), so RLS decides which rows change:
    a photo's uploader while they hold at least project_member on its project
    (ww-backend #277), or a project_admin of the project. Any other id, including a plain
    member's request for a photo someone else uploaded, changes 0 rows without an error, as
    does an id already deleted. ``deleted_ids`` lists the rows that changed and
    ``skipped_ids`` the rest; ``deleted`` counts ``deleted_ids``.
    """
    req_id = getattr(request.state, "request_id", None)
    now = now_iso()
    deleted, skipped = await asyncio.to_thread(soft_delete_media_as_user, user_client, body.media_ids, now)
    return ApiResponse(
        # deleted_at lets the client offer an Undo (POST /batch/restore).
        data={
            "deleted": len(deleted),
            "requested": len(body.media_ids),
            "deleted_at": now,
            "deleted_ids": deleted,
            "skipped_ids": skipped,
        },
        meta=ApiMeta(request_id=req_id),
    )


class RestoreMediaRequest(BaseModel):
    """Undo a media soft-delete."""

    media_ids: list[str] = Field(..., max_length=500)
    deleted_at: str


@router.post("/batch/restore", dependencies=[Depends(require_not_demo)])
async def batch_restore_media(
    request: Request,
    body: RestoreMediaRequest,
    user=Depends(get_current_user),
    user_client=Depends(get_user_client),
):
    """Undo a media delete, clearing ``deleted_at`` where it equals the given timestamp. Runs as the
    user, so RLS applies the delete rule: the photo's uploader while they hold at least
    project_member, or a project_admin of the project; other ids change 0 rows without an error.
    Scoping by the exact timestamp restores only the photos removed in that delete.
    ``restored_ids`` lists the rows that changed and ``skipped_ids`` the rest."""
    req_id = getattr(request.state, "request_id", None)
    restored, skipped = await asyncio.to_thread(restore_media_as_user, user_client, body.media_ids, body.deleted_at)
    return ApiResponse(
        data={"restored": len(restored), "restored_ids": restored, "skipped_ids": skipped},
        meta=ApiMeta(request_id=req_id),
    )


class RunSelectedRequest(BaseModel):
    """Queue an AI run on selected photos (the grid's *Run AI again*)."""

    media_ids: list[str] = Field(..., max_length=200)
    steps: list[PipelineStepType] = Field(default=[PipelineStepType.SPECIESNET], min_length=1, description="Pipeline steps to run, in order")
    confidence_threshold: float = Field(0.2, ge=0.0, le=1.0)


@router.post("/run-selected", dependencies=[Depends(require_not_demo)])
async def run_pipeline_selected(
    request: Request,
    body: RunSelectedRequest,
    user=Depends(get_verified_user),
):
    """Queue an AI run on the selected photos and answer with its job.

    The photos may span deployments: one ``ai_run`` job covers them all and runs the steps on
    those photos only. Photos in projects the caller cannot change are left out (``403`` when
    that is all of them). ``PIPELINE_BUSY`` while an AI job is queued or running on any of their
    deployments. On AWS the job waits for the next REANNZ GPU batch.
    """
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_PIPELINE_ENABLED:
        return ApiResponse(
            error=ApiError(code="FEATURE_DISABLED", message="The AI pipeline is disabled (FF_PIPELINE_ENABLED)."),
            meta=ApiMeta(request_id=req_id),
        )
    if not body.media_ids:
        return ApiResponse(
            error=ApiError(code="VALIDATION_ERROR", message="media_ids cannot be empty."),
            meta=ApiMeta(request_id=req_id),
        )
    # Object-level authz: the run writes with the service role, so only photos the caller may change.
    media_ids = await writable_media_ids(user.id, body.media_ids)
    if not media_ids:
        raise HTTPException(status_code=403, detail=WRITE_REFUSED)

    try:
        job_id, deployment_ids = await start_selected_run(media_ids, user.id, body.steps, body.confidence_threshold)
    except PipelineBusyError as e:
        return ApiResponse(
            error=ApiError(code="PIPELINE_BUSY", message=f"{e}. Try again when it finishes.", retryable=True),
            meta=ApiMeta(request_id=req_id),
        )
    except ValueError as e:
        return ApiResponse(error=ApiError(code="INVALID_REQUEST", message=str(e)), meta=ApiMeta(request_id=req_id))
    except Exception as e:
        logger.error("run_selected_error", error=str(e), photos=len(media_ids))
        return ApiResponse(
            error=ApiError(code="PIPELINE_ERROR", message=f"Could not queue the AI run: {e}", retryable=True),
            meta=ApiMeta(request_id=req_id),
        )
    return ApiResponse(
        data={"job_id": job_id, "status": "queued", "deployment_ids": deployment_ids, "media_count": len(media_ids)},
        meta=ApiMeta(request_id=req_id),
    )
