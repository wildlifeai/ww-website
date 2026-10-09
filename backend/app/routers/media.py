# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Media serving endpoints — proxy images from private storage.

GET /api/media/{media_id}/image?size=thumb → low-res thumbnail (grid)
GET /api/media/{media_id}/image?size=full  → full resolution (detail panel)
"""

import asyncio
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.authz import require_deployment_access, require_media_access
from app.config import settings
from app.dependencies import get_current_user, get_user_client, require_not_demo
from app.domain.media_registry import resolve_url, with_resolved_urls
from app.domain.media_resolver import resolve_media
from app.schemas.common import ApiError, ApiMeta, ApiResponse
from app.services import supabase_client

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

    result = client.table("media").select("file_path").eq("id", media_id).maybe_single().execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Media record not found")

    file_path = result.data.get("file_path", "")

    resolved = await resolve_media(file_path, size=size)
    if not resolved:
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

    image_bytes, content_type = resolved
    # Thumbnails cache longer (10min), full-res shorter (5min) since they're on-demand
    cache_ttl = 600 if size == "thumb" else 300
    return Response(
        content=image_bytes,
        media_type=content_type,
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
    """Return a display URL for a media item regardless of storage provider."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_MEDIA_REGISTRY_ENABLED:
        return _registry_disabled(req_id)

    client = supabase_client.create_anon_client()
    result = client.table("media").select(_REGISTRY_SELECT).eq("id", media_id).maybe_single().execute()
    if not result.data:
        return ApiResponse(error=ApiError(code="NOT_FOUND", message="Media not found"), meta=ApiMeta(request_id=req_id))

    return ApiResponse(
        data={"media_id": media_id, "size": size, "url": resolve_url(result.data, size)},
        meta=ApiMeta(request_id=req_id),
    )


@router.get("/registry/{deployment_id}", dependencies=[Depends(require_deployment_access)])
async def media_registry(
    request: Request,
    deployment_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=500),
    user=Depends(get_current_user),
):
    """Paginated media list with pre-resolved URLs — primary source for the grid."""
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
    rows = [with_resolved_urls(r) for r in (result.data or [])]
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

    from app.jobs.definitions import backfill_thumbnails_job
    from app.jobs.runner import enqueue_local_job
    from app.jobs.store import create_job

    # Owner and deployment let the job show in the user's processing history and
    # drive the Annotations "being processed" banner for that deployment.
    job_id = await create_job(
        user_id=user.id,
        kind="media_prep",
        label=f"Thumbnails for deployment {deployment_id[:8]}",
        deployment_ids=[deployment_id],
    )
    enqueue_local_job(backfill_thumbnails_job(job_id, deployment_id))
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

    Idempotent: already-deleted media are silently skipped. Runs as the
    requesting user (not the service role) so RLS restricts the update to
    media in projects where they hold project_member — IDs outside their
    projects are silently ignored, never deleted.
    """
    req_id = getattr(request.state, "request_id", None)
    now = datetime.now(timezone.utc).isoformat()

    def _delete():
        result = user_client.table("media").update({"deleted_at": now}).in_("id", body.media_ids).is_("deleted_at", "null").execute()
        return len(result.data or [])

    deleted = await asyncio.to_thread(_delete)
    return ApiResponse(
        # deleted_at lets the client offer an Undo (POST /batch/restore).
        data={"deleted": deleted, "requested": len(body.media_ids), "deleted_at": now},
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
    """Undo a media delete — clears ``deleted_at`` where it equals the given timestamp. Runs as the
    user so RLS keeps it to their own projects; scoping by the exact timestamp restores only the
    photos removed in that delete."""
    req_id = getattr(request.state, "request_id", None)

    def _restore():
        result = user_client.table("media").update({"deleted_at": None}).in_("id", body.media_ids).eq("deleted_at", body.deleted_at).execute()
        return len(result.data or [])

    restored = await asyncio.to_thread(_restore)
    return ApiResponse(data={"restored": restored}, meta=ApiMeta(request_id=req_id))


class RunSelectedRequest(BaseModel):
    """Queue a pipeline run for specific media IDs."""

    media_ids: list[str] = Field(..., max_length=200)
    steps: list[str] = Field(default=["speciesnet"], description="Pipeline step types to run")
    confidence_threshold: float = Field(0.2, ge=0.0, le=1.0)


@router.post("/run-selected", dependencies=[Depends(require_not_demo)])
async def run_pipeline_selected(
    request: Request,
    body: RunSelectedRequest,
    user=Depends(get_current_user),
    user_client=Depends(get_user_client),
):
    """Queue a pipeline run for specific media IDs (not the full deployment).

    Looks up the deployment_id from the first media record, then runs the
    pipeline with a media_ids filter. The lookup runs as the requesting user
    so RLS limits it to media in their own projects.
    """
    req_id = getattr(request.state, "request_id", None)
    if not body.media_ids:
        return ApiResponse(
            error=ApiError(code="VALIDATION_ERROR", message="media_ids cannot be empty."),
            meta=ApiMeta(request_id=req_id),
        )

    def _lookup():
        resp = user_client.table("media").select("deployment_id").in_("id", body.media_ids[:1]).limit(1).execute()
        return resp.data[0]["deployment_id"] if resp.data else None

    deployment_id = await asyncio.to_thread(_lookup)
    if not deployment_id:
        return ApiResponse(
            error=ApiError(code="NOT_FOUND", message="No media found for the given IDs."),
            meta=ApiMeta(request_id=req_id),
        )

    # TODO: run_pipeline_job does not exist in app/jobs/definitions.py yet —
    # enqueueing it raised at runtime. Surface NOT_IMPLEMENTED honestly until
    # the per-media pipeline job lands, then restore:
    #   job_id = await create_job()
    #   await enqueue_job("run_pipeline_job", job_id, deployment_id,
    #                     body.steps, body.confidence_threshold, body.media_ids)
    return ApiResponse(
        error=ApiError(
            code="NOT_IMPLEMENTED",
            message="Running the pipeline on selected media is not available yet.",
        ),
        meta=ApiMeta(request_id=req_id),
    )
