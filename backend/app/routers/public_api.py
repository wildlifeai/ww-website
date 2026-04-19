# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Public Data API router — /api/v1/* endpoints for external partners.

Authentication via X-API-Key header (organisation-scoped).
Gated behind FF_PUBLIC_API_ENABLED feature flag.
"""

from fastapi import APIRouter, HTTPException, Header, Request, Depends, Query
from typing import Optional, List

from app.config import settings
from app.schemas.common import ApiResponse, ApiMeta
from app.schemas.public_api import (
    ApiKeyCreate,
    ApiKeyResponse,
    ApiKeyInfo,
    CamtrapDPExportRequest,
)
from app.schemas.job import JobCreateResponse
from app.services.api_key import (
    validate_api_key,
    create_api_key_record,
    revoke_api_key,
    list_api_keys,
    ApiKeyError,
)
from app.domain.public_api import (
    list_deployments,
    get_deployment,
    list_devices,
    get_telemetry,
    list_observations,
    PublicApiError,
)
from app.dependencies import get_current_user
from app.jobs.store import create_job

import structlog

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

@router.post("/api-keys")
async def create_key(
    body: ApiKeyCreate,
    request: Request,
    user=Depends(get_current_user),
):
    """Create a new API key for the user's organisation.

    Only organisation admins can create keys. The raw key is returned
    once — it cannot be retrieved again.
    """
    if not settings.FF_PUBLIC_API_ENABLED:
        raise HTTPException(404, detail="Public API is not enabled")

    # TODO: Get org_id from user's admin role
    # For now, use a query param or the user's primary org
    try:
        from app.services.supabase_client import create_service_client

        client = create_service_client()
        roles = (
            client.table("user_roles")
            .select("organisation_id, role")
            .eq("user_id", user.id)
            .in_("role", ["admin", "owner"])
            .execute()
        )

        if not roles.data:
            raise HTTPException(403, detail="Only organisation admins can create API keys")

        org_id = roles.data[0]["organisation_id"]

        raw_key, record = await create_api_key_record(
            org_id=org_id,
            user_id=user.id,
            name=body.name,
            scopes=body.scopes,
            expires_at=body.expires_at,
        )

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

    except ApiKeyError as e:
        raise HTTPException(400, detail=str(e))


@router.get("/api-keys")
async def list_keys(
    request: Request,
    user=Depends(get_current_user),
):
    """List active API keys for the user's organisation."""
    if not settings.FF_PUBLIC_API_ENABLED:
        raise HTTPException(404, detail="Public API is not enabled")

    from app.services.supabase_client import create_service_client

    client = create_service_client()
    roles = (
        client.table("user_roles")
        .select("organisation_id")
        .eq("user_id", user.id)
        .in_("role", ["admin", "owner"])
        .execute()
    )

    if not roles.data:
        raise HTTPException(403, detail="Only organisation admins can view API keys")

    org_id = roles.data[0]["organisation_id"]
    keys = await list_api_keys(org_id)

    return ApiResponse(
        data=[ApiKeyInfo(**k).model_dump() for k in keys],
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


@router.delete("/api-keys/{key_id}")
async def revoke_key(
    key_id: str,
    request: Request,
    user=Depends(get_current_user),
):
    """Revoke an API key."""
    if not settings.FF_PUBLIC_API_ENABLED:
        raise HTTPException(404, detail="Public API is not enabled")

    from app.services.supabase_client import create_service_client

    client = create_service_client()
    roles = (
        client.table("user_roles")
        .select("organisation_id")
        .eq("user_id", user.id)
        .in_("role", ["admin", "owner"])
        .execute()
    )

    if not roles.data:
        raise HTTPException(403, detail="Only organisation admins can revoke API keys")

    org_id = roles.data[0]["organisation_id"]
    success = await revoke_api_key(key_id, org_id)

    if not success:
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
        data = await get_telemetry(
            key["organisation_id"], device_eui, date_from, date_to, limit
        )
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
    records, total = await list_observations(
        key["organisation_id"], deployment_id, limit, offset
    )

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

    job_id = await create_job()

    from app.jobs.runner import enqueue_local_job
    from app.jobs.definitions import export_camtrapdp_job
    
    enqueue_local_job(export_camtrapdp_job(
        job_id=job_id,
        org_id=key["organisation_id"],
        params=body.model_dump(),
    ))

    return ApiResponse(
        data=JobCreateResponse(job_id=job_id).model_dump(),
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )
