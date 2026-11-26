# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reports.

GET /api/reports/gbf → the GBF contribution report of one or more projects over a period (#371),
built in ``domain/gbf_report.py`` from the database's counts, which run as the caller (#456).
Gated by ``FF_GBF_REPORT_ENABLED``: off, it answers FEATURE_DISABLED.
"""

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.authz import assert_project_viewer
from app.config import settings
from app.dependencies import get_current_user, get_user_client
from app.domain.gbf_report import gbf_report
from app.middleware.rate_limit import limiter
from app.schemas.common import ApiError, ApiMeta, ApiResponse

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _project_ids(value: str) -> list[str]:
    try:
        ids = list(dict.fromkeys(str(uuid.UUID(p.strip())) for p in value.split(",") if p.strip()))
    except ValueError:
        raise HTTPException(status_code=422, detail="project_ids must be project ids separated by commas.")
    if not ids:
        raise HTTPException(status_code=422, detail="Give at least one project id.")
    return ids


@router.get("/gbf")
@limiter.limit("10/minute")
async def get_gbf_report(
    request: Request,
    project_ids: str = Query(..., description="Comma-separated project ids"),
    date_from: date = Query(..., alias="from", description="First day of the period (UTC)"),
    date_to: date = Query(..., alias="to", description="Last day of the period (UTC), included"),
    user=Depends(get_current_user),
    user_client: Any = Depends(get_user_client),
):
    """The GBF contribution report for the projects over the period. It only reads, so the demo
    account may call it. The photos are counted with the caller's session, so the caller needs
    what the database reads them by on every project (``authz._can_view``): a project role,
    ``organisation_manager`` of its organisation, or ``ww_admin``; ``404`` otherwise."""
    meta = ApiMeta(request_id=getattr(request.state, "request_id", None))
    if not settings.FF_GBF_REPORT_ENABLED:
        return ApiResponse(error=ApiError(code="FEATURE_DISABLED", message="The GBF report is disabled (FF_GBF_REPORT_ENABLED)."), meta=meta)

    ids = _project_ids(project_ids)
    if date_from > date_to:
        raise HTTPException(status_code=422, detail="'from' is after 'to'.")
    for project_id in ids:
        await assert_project_viewer(user.id, project_id)

    report = await gbf_report(user_client, ids, date_from, date_to)
    if report is None:
        raise HTTPException(status_code=404, detail="Not found")
    return ApiResponse(data=report, meta=meta)
