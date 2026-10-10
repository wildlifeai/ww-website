# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Public Data API router — /api/v1/* endpoints for external partners.

Authentication via X-API-Key header (organisation-scoped). Key management
(/api/v1/api-keys) uses the JWT and is for the organisation's managers.
Gated behind FF_PUBLIC_API_ENABLED feature flag.
"""

import uuid
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from app.authz import org_manager_right
from app.config import settings
from app.dependencies import get_current_user, require_not_demo
from app.domain.public_api import (
    PublicApiError,
    get_deployment,
    get_telemetry,
    list_deployments,
    list_devices,
    list_observations,
)
from app.jobs.store import create_job
from app.schemas.common import ApiError, ApiMeta, ApiResponse
from app.schemas.job import JobCreateResponse
from app.schemas.public_api import (
    ApiKeyCreate,
    ApiKeyInfo,
    ApiKeyResponse,
    CamtrapDPExportRequest,
)
from app.services.api_key import (
    ApiKeyError,
    create_api_key_record,
    list_api_keys,
    revoke_api_key,
    validate_api_key,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["public-api"])


# ── Auth dependency ──────────────────────────────────────────────────


async def require_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    required_scope: Optional[str] = None,
):
    """Validate API key and check feature flag."""
    if not settings.FF_PUBLIC_API_ENABLED:
        raise HTTPException(404, detail="Public API is not enabled")

    try:
        key_record = await validate_api_key(x_api_key, required_scope)
        return key_record
    except ApiKeyError as e:
        raise HTTPException(401, detail=str(e))


async def require_scope(scope: str):
    """Create a dependency that requires a specific scope."""

    async def _check(x_api_key: str = Header(..., alias="X-API-Key")):
        return await require_api_key(x_api_key, required_scope=scope)

    return _check


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
    project_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    """List deployments for the organisation attached to the API key."""
    key = await require_api_key(x_api_key, required_scope="deployments:read")
    org_id = key["organisation_id"]

    records, total = await list_deployments(org_id, project_id, status, limit, offset)

    return ApiResponse(
        data=records,
        meta=ApiMeta(
            request_id=getattr(request.state, "request_id", None),
            total=total,
            page=(offset // limit) + 1,
        ),
    )


@router.get("/deployments/{deployment_id}")
async def api_get_deployment(
    deployment_id: str,
    request: Request,
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    """Get a single deployment by ID."""
    key = await require_api_key(x_api_key, required_scope="deployments:read")
    record = await get_deployment(key["organisation_id"], deployment_id)

    if not record:
        raise HTTPException(404, detail="Deployment not found")

    return ApiResponse(
        data=record,
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


@router.get("/devices")
async def api_list_devices(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    """List devices for the organisation."""
    key = await require_api_key(x_api_key, required_scope="devices:read")
    records, total = await list_devices(key["organisation_id"], limit, offset)

    return ApiResponse(
        data=records,
        meta=ApiMeta(
            request_id=getattr(request.state, "request_id", None),
            total=total,
        ),
    )


@router.get("/devices/{device_eui}/telemetry")
async def api_device_telemetry(
    device_eui: str,
    request: Request,
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    """Get telemetry time-series for a specific device."""
    key = await require_api_key(x_api_key, required_scope="telemetry:read")

    try:
        data = await get_telemetry(key["organisation_id"], device_eui, date_from, date_to, limit)
    except PublicApiError as e:
        raise HTTPException(404, detail=str(e))

    return ApiResponse(
        data=data,
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


@router.get("/observations")
async def api_list_observations(
    request: Request,
    deployment_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    """List AI detection observations."""
    key = await require_api_key(x_api_key, required_scope="observations:read")
    records, total = await list_observations(key["organisation_id"], deployment_id, limit, offset)

    return ApiResponse(
        data=records,
        meta=ApiMeta(
            request_id=getattr(request.state, "request_id", None),
            total=total,
        ),
    )


@router.post("/export/camtrapdp")
async def api_export_camtrapdp(
    body: CamtrapDPExportRequest,
    request: Request,
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    """Export deployment data as a CamtrapDP package (async job)."""
    key = await require_api_key(x_api_key, required_scope="export:camtrapdp")

    # API-key (machine) job — no logged-in user, so it isn't tied to a user's
    # processing history; still stamped with a kind/label for traceability.
    job_id = await create_job(kind="export", label="CamtrapDP export (API)")

    from app.jobs.definitions import export_camtrapdp_job
    from app.jobs.runner import enqueue_local_job

    enqueue_local_job(
        export_camtrapdp_job(
            job_id=job_id,
            org_id=key["organisation_id"],
            params=body.model_dump(),
        )
    )

    return ApiResponse(
        data=JobCreateResponse(job_id=job_id).model_dump(),
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )
