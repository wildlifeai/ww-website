# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Conservation intelligence router — shift detection, health, alerts, occupancy.

Thin HTTP layer (envelope responses); analytics live in domain/intelligence.py.
Gated behind ``FF_INTELLIGENCE_ENABLED``.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Request

from app.authz import require_deployment_access, require_deployment_write, require_org_access, require_project_access
from app.config import settings
from app.dependencies import get_current_user, require_not_demo
from app.schemas.common import ApiError, ApiMeta, ApiResponse
from app.schemas.intelligence import ShiftDetectionRequest

logger = structlog.get_logger()

router = APIRouter(prefix="/api/intelligence", tags=["intelligence"])


def _disabled(req_id):
    return ApiResponse(
        error=ApiError(code="FEATURE_DISABLED", message="Conservation intelligence is disabled (FF_INTELLIGENCE_ENABLED)."),
        meta=ApiMeta(request_id=req_id),
    )


@router.post("/shift-detection/{deployment_id}", dependencies=[Depends(require_deployment_write), Depends(require_not_demo)])
async def shift_detection(request: Request, deployment_id: str, body: ShiftDetectionRequest, user=Depends(get_current_user)):
    """Detect ecological distribution shift between two time windows."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_INTELLIGENCE_ENABLED:
        return _disabled(req_id)

    from app.domain.intelligence import detect_distribution_shift

    result = await detect_distribution_shift(
        deployment_id,
        (body.period_a_start, body.period_a_end),
        (body.period_b_start, body.period_b_end),
    )
    return ApiResponse(data=result, meta=ApiMeta(request_id=req_id))


@router.get("/health/{project_id}", dependencies=[Depends(require_project_access)])
async def health_report(request: Request, project_id: str, user=Depends(get_current_user)):
    """Dataset health: species coverage, review funnel, outlier rate."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_INTELLIGENCE_ENABLED:
        return _disabled(req_id)

    from app.domain.intelligence import dataset_health

    return ApiResponse(data=await dataset_health(project_id), meta=ApiMeta(request_id=req_id))


@router.get("/alerts/{project_id}", dependencies=[Depends(require_project_access)])
async def alerts(request: Request, project_id: str, user=Depends(get_current_user)):
    """Active (unacknowledged) conservation alerts for a project."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_INTELLIGENCE_ENABLED:
        return _disabled(req_id)

    from app.domain.intelligence import list_alerts

    rows = await list_alerts(project_id)
    return ApiResponse(data={"alerts": rows, "count": len(rows)}, meta=ApiMeta(request_id=req_id))


@router.get("/unknown-species/{org_id}", dependencies=[Depends(require_org_access)])
async def unknown_species_endpoint(request: Request, org_id: str, user=Depends(get_current_user)):
    """Candidate (provisional) taxa awaiting expert confirmation."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_INTELLIGENCE_ENABLED:
        return _disabled(req_id)

    from app.domain.intelligence import unknown_species

    rows = await unknown_species(org_id)
    return ApiResponse(data={"candidates": rows, "count": len(rows)}, meta=ApiMeta(request_id=req_id))


@router.get("/occupancy/{project_id}", dependencies=[Depends(require_project_access)])
async def occupancy_endpoint(request: Request, project_id: str, user=Depends(get_current_user)):
    """Species-assemblage overlap (Jaccard) between deployments."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_INTELLIGENCE_ENABLED:
        return _disabled(req_id)

    from app.domain.intelligence import occupancy

    return ApiResponse(data=await occupancy(project_id), meta=ApiMeta(request_id=req_id))


@router.get("/accumulation/{deployment_id}", dependencies=[Depends(require_deployment_access)])
async def accumulation_endpoint(request: Request, deployment_id: str, user=Depends(get_current_user)):
    """Species accumulation curve over time for a deployment."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_INTELLIGENCE_ENABLED:
        return _disabled(req_id)

    from app.domain.intelligence import accumulation

    return ApiResponse(data=await accumulation(deployment_id), meta=ApiMeta(request_id=req_id))
