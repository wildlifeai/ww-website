# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Public Data API router — /api/v1/* endpoints for external partners.

Authentication via X-API-Key header (organisation-scoped). Key management
(/api/v1/api-keys) uses the JWT and is for the organisation's managers.
Gated behind FF_PUBLIC_API_ENABLED feature flag. The partner guide is
documentation/resources/public-api-guide.md.
"""

import uuid
from dataclasses import asdict
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import APIKeyHeader

from app.authz import org_manager_right
from app.config import settings
from app.dependencies import get_current_user, require_not_demo
from app.domain.camtrapdp_export import ExportCaller, ExportSelection
from app.domain.public_api import (
    MAX_PAGE,
    get_deployment,
    get_telemetry,
    list_deployments,
    list_devices,
    list_observations,
    project_in_organisation,
)
from app.jobs.dispatch import enqueue_job
from app.jobs.store import create_job, get_job
from app.middleware.rate_limit import hit_api_key_limit
from app.schemas.common import ApiError, ApiMeta, ApiResponse
from app.schemas.exports import CamtrapExportRequest
from app.schemas.job import JobCreateResponse
from app.schemas.public_api import (
    ApiJobOut,
    ApiJobResponse,
    ApiKeyCreate,
    ApiKeyInfo,
    ApiKeyResponse,
    ObservationOut,
    ObservationsResponse,
    TelemetryPoint,
    TelemetryResponse,
)
from app.services.api_key import (
    ApiKeyError,
    ApiKeyScopeError,
    create_api_key_record,
    list_api_keys,
    revoke_api_key,
    validate_api_key,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["public-api"])

# An export queues a job that reads every original photo, so a key may start fewer, as on
# the Download button (POST /api/exports/camtrapdp).
EXPORT_RATE_LIMIT_PER_MINUTE = 5


# ── Auth dependency ──────────────────────────────────────────────────

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="An organisation API key, ww_live_...")


def require_api_key(scope: str, *, per_minute: Optional[int] = None, bucket: str = "all"):
    """Dependency: a valid key with ``scope``, within its rate limit. Returns the key's row.

    404 while the API is off, 401 for a missing, unknown, revoked or expired key, 403 for a
    key without the scope, 429 with Retry-After once the key has used its calls for the
    minute (``PUBLIC_API_RATE_LIMIT_PER_MINUTE``, and ``per_minute`` in its own ``bucket``).
    """

    async def _check(x_api_key: Optional[str] = Depends(_api_key_header)) -> dict:
        if not settings.FF_PUBLIC_API_ENABLED:
            raise HTTPException(404, detail="Public API is not enabled")
        if not x_api_key:
            raise HTTPException(401, detail="Missing X-API-Key header")
        try:
            key = await validate_api_key(x_api_key, scope)
        except ApiKeyScopeError as e:
            raise HTTPException(403, detail=str(e))
        except ApiKeyError as e:
            raise HTTPException(401, detail=str(e))

        limits = [(settings.PUBLIC_API_RATE_LIMIT_PER_MINUTE, "all")]
        if per_minute is not None:
            limits.append((per_minute, bucket))
        for n, name in limits:
            retry_after = hit_api_key_limit(key["id"], n, name)
            if retry_after is not None:
                raise HTTPException(
                    429,
                    detail=f"Rate limit exceeded: {n} calls a minute for this key. Retry after {retry_after} s.",
                    headers={"Retry-After": str(retry_after)},
                )
        return key

    return _check


def _meta(request: Request, **kwargs) -> ApiMeta:
    return ApiMeta(request_id=getattr(request.state, "request_id", None), **kwargs)


def _page(offset: int, limit: int) -> int:
    return (offset // limit) + 1


# ── API Key Management (JWT auth, not API key auth) ──────────────────


def _disabled(request: Request) -> ApiResponse:
    return ApiResponse(
        error=ApiError(code="FEATURE_DISABLED", message="The public API is disabled (FF_PUBLIC_API_ENABLED)."),
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


async def _require_org_manager(user_id: str, org_id: str) -> None:
    """``organisation_manager`` of the organisation (``org_manager_right``): 403 for anyone
    else with a role reaching it, 404 otherwise."""
    right = await org_manager_right(user_id, org_id)
    if right == "not_found":
        raise HTTPException(404, detail="Organisation not found")
    if right == "refused":
        raise HTTPException(403, detail="Only the organisation's managers can manage its API keys")


@router.post("/api-keys", dependencies=[Depends(require_not_demo)])
async def create_key(
    body: ApiKeyCreate,
    request: Request,
    user=Depends(get_current_user),
):
    """Create an API key for an organisation the caller manages.

    The raw key is returned once. It cannot be retrieved again.
    """
    if not settings.FF_PUBLIC_API_ENABLED:
        return _disabled(request)

    org_id = str(body.organisation_id)
    await _require_org_manager(user.id, org_id)

    try:
        raw_key, record = await create_api_key_record(
            org_id=org_id,
            user_id=user.id,
            name=body.name,
            scopes=body.scopes,
            expires_at=body.expires_at,
        )
    except ApiKeyError as e:
        raise HTTPException(400, detail=str(e))

    return ApiResponse(
        data=ApiKeyResponse(
            id=record["id"],
            name=record["name"],
            key=raw_key,
            key_prefix=record["key_prefix"],
            scopes=record["scopes"],
            expires_at=record.get("expires_at"),
            created_at=record.get("created_at"),
        ).model_dump(),
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


@router.get("/api-keys")
async def list_keys(
    request: Request,
    organisation_id: uuid.UUID = Query(..., description="Organisation whose keys to list"),
    user=Depends(get_current_user),
):
    """List an organisation's active (unrevoked) API keys. Managers only."""
    if not settings.FF_PUBLIC_API_ENABLED:
        return _disabled(request)

    org_id = str(organisation_id)
    await _require_org_manager(user.id, org_id)
    keys = await list_api_keys(org_id)

    return ApiResponse(
        data=[ApiKeyInfo(**k).model_dump() for k in keys],
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


@router.delete("/api-keys/{key_id}", dependencies=[Depends(require_not_demo)])
async def revoke_key(
    key_id: uuid.UUID,
    request: Request,
    organisation_id: uuid.UUID = Query(..., description="Organisation the key belongs to"),
    user=Depends(get_current_user),
):
    """Revoke one of an organisation's API keys. Managers only."""
    if not settings.FF_PUBLIC_API_ENABLED:
        return _disabled(request)

    org_id = str(organisation_id)
    await _require_org_manager(user.id, org_id)

    if not await revoke_api_key(str(key_id), org_id):
        raise HTTPException(404, detail="API key not found or already revoked")

    return ApiResponse(
        data={"revoked": True},
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


# ── Data Endpoints (API key auth) ────────────────────────────────────


@router.get("/deployments")
async def api_list_deployments(
    request: Request,
    project_id: Optional[uuid.UUID] = Query(None),
    status: Optional[str] = Query(None, description="A deployment status value: planned, started or ended"),
    limit: int = Query(50, ge=1, le=MAX_PAGE),
    offset: int = Query(0, ge=0),
    key: dict = Depends(require_api_key("deployments:read")),
):
    """List the deployments in the organisation's projects, newest first."""
    records, total = await list_deployments(key["organisation_id"], str(project_id) if project_id else None, status, limit, offset)
    return ApiResponse(data=records, meta=_meta(request, total=total, page=_page(offset, limit)))


@router.get("/deployments/{deployment_id}")
async def api_get_deployment(
    deployment_id: uuid.UUID,
    request: Request,
    key: dict = Depends(require_api_key("deployments:read")),
):
    """Get one deployment in the organisation's projects."""
    record = await get_deployment(key["organisation_id"], str(deployment_id))
    if not record:
        raise HTTPException(404, detail="Deployment not found")
    return ApiResponse(data=record, meta=_meta(request))


@router.get("/devices")
async def api_list_devices(
    request: Request,
    limit: int = Query(50, ge=1, le=MAX_PAGE),
    offset: int = Query(0, ge=0),
    key: dict = Depends(require_api_key("devices:read")),
):
    """List the organisation's own cameras."""
    records, total = await list_devices(key["organisation_id"], limit, offset)
    return ApiResponse(data=records, meta=_meta(request, total=total, page=_page(offset, limit)))


@router.get("/devices/{device_eui}/telemetry", response_model=TelemetryResponse)
async def api_device_telemetry(
    device_eui: str,
    request: Request,
    date_from: Optional[str] = Query(None, description="ISO 8601; messages received at or after"),
    date_to: Optional[str] = Query(None, description="ISO 8601; messages received at or before"),
    limit: int = Query(200, ge=1, le=MAX_PAGE),
    key: dict = Depends(require_api_key("telemetry:read")),
):
    """A camera's LoRaWAN messages from its deployments in the organisation's projects, newest first."""
    data = await get_telemetry(key["organisation_id"], device_eui, date_from, date_to, limit)
    return TelemetryResponse(data=[TelemetryPoint(**p) for p in data], meta=_meta(request))


@router.get("/observations", response_model=ObservationsResponse)
async def api_list_observations(
    request: Request,
    project_id: Optional[uuid.UUID] = Query(None),
    deployment_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(100, ge=1, le=MAX_PAGE),
    offset: int = Query(0, ge=0),
    key: dict = Depends(require_api_key("observations:read")),
):
    """One verdict per photo, as the website's photo grid shows it.

    Photos with at least one observation, in the organisation's projects, in the order they
    were registered. A page is at most 1,000 photos; page with ``offset``.
    """
    records, total = await list_observations(
        key["organisation_id"],
        str(project_id) if project_id else None,
        str(deployment_id) if deployment_id else None,
        limit,
        offset,
    )
    return ObservationsResponse(data=[ObservationOut(**r) for r in records], meta=_meta(request, total=total, page=_page(offset, limit)))


@router.post("/export/camtrapdp")
async def api_export_camtrapdp(
    body: CamtrapExportRequest,
    request: Request,
    key: dict = Depends(require_api_key("export:camtrapdp", per_minute=EXPORT_RATE_LIMIT_PER_MINUTE, bucket="export")),
):
    """Start a CamtrapDP export of one project with its original photos, as the Download button does.

    Returns ``{job_id}``; poll ``GET /api/v1/jobs/{job_id}`` for the signed link. Behind
    ``FF_CAMTRAPDP_EXPORT_ENABLED``, like the Download button's export.
    """
    if not settings.FF_CAMTRAPDP_EXPORT_ENABLED:
        raise HTTPException(404, detail="CamtrapDP export is not enabled")
    org_id = key["organisation_id"]
    project_id = str(body.project_id)
    if not await project_in_organisation(org_id, project_id):
        raise HTTPException(404, detail="Project not found")

    selection = ExportSelection(
        project_id=project_id,
        deployment_ids=[str(d) for d in body.deployment_ids],
        date_from=body.date_from.isoformat() if body.date_from else None,
        date_to=body.date_to.isoformat() if body.date_to else None,
    )
    job_id = await create_job(kind="export", label="CamtrapDP export with photos (API)", organisation_id=org_id)
    await enqueue_job("export_camtrapdp_originals_job", job_id, asdict(selection), asdict(ExportCaller(organisation_id=org_id)))
    logger.info("public_api_export_started", job_id=job_id, org_id=org_id, key_id=key["id"])
    return ApiResponse(data=JobCreateResponse(job_id=job_id).model_dump(), meta=_meta(request))


@router.get("/jobs/{job_id}", response_model=ApiJobResponse)
async def api_get_job(
    job_id: uuid.UUID,
    request: Request,
    key: dict = Depends(require_api_key("export:camtrapdp")),
):
    """A job one of the organisation's keys started: status, progress, and the signed link once done.

    Another organisation's job, or a signed-in user's, answers 404 like a missing one.
    """
    job = await get_job(str(job_id))
    if not job or job.organisation_id != key["organisation_id"]:
        raise HTTPException(404, detail="Job not found")
    out = ApiJobOut(
        job_id=job.job_id,
        status=job.status,
        progress=job.progress,
        message=job.message,
        error=job.error,
        result_url=job.result_url,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )
    return ApiJobResponse(data=out, meta=_meta(request))
