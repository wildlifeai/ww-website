# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Data exports.

POST /api/exports/camtrapdp → starts the CamtrapDP export job (#328), returns {job_id}.
Poll it with GET /api/jobs/{id}; its result_url is the signed download link.
Gated by ``FF_CAMTRAPDP_EXPORT_ENABLED``: off, the endpoint answers FEATURE_DISABLED.
"""

from dataclasses import asdict

from fastapi import APIRouter, Depends, Header, Request

from app.authz import assert_access
from app.config import settings
from app.dependencies import get_current_user
from app.domain.camtrapdp_export import ExportCaller, ExportSelection
from app.jobs.dispatch import enqueue_job
from app.jobs.store import create_job
from app.middleware.rate_limit import limiter
from app.schemas.common import ApiError, ApiMeta, ApiResponse
from app.schemas.exports import CamtrapExportRequest
from app.schemas.job import JobCreateResponse

router = APIRouter(prefix="/api/exports", tags=["exports"])


@router.post("/camtrapdp")
@limiter.limit("5/minute")
async def start_camtrapdp_export(
    request: Request,
    body: CamtrapExportRequest,
    authorization: str = Header(...),
    user=Depends(get_current_user),
):
    """Start a CamtrapDP export of one project with its original photos.

    The job calls ww-backend's export-camtrap-dp as the caller, so its project-membership
    check decides. The 404 here only saves a job for a project in another organisation.
    """
    meta = ApiMeta(request_id=getattr(request.state, "request_id", None))
    if not settings.FF_CAMTRAPDP_EXPORT_ENABLED:
        return ApiResponse(error=ApiError(code="FEATURE_DISABLED", message="CamtrapDP export is disabled (FF_CAMTRAPDP_EXPORT_ENABLED)."), meta=meta)

    project_id = str(body.project_id)
    await assert_access(user.id, project_id=project_id)
    selection = ExportSelection(
        project_id=project_id,
        deployment_ids=[str(d) for d in body.deployment_ids],
        date_from=body.date_from.isoformat() if body.date_from else None,
        date_to=body.date_to.isoformat() if body.date_to else None,
    )
    caller = ExportCaller(user_token=authorization.removeprefix("Bearer ").strip())

    job_id = await create_job(user_id=user.id, kind="export", label="CamtrapDP export with photos")
    await enqueue_job("export_camtrapdp_originals_job", job_id, asdict(selection), asdict(caller))
    return ApiResponse(data=JobCreateResponse(job_id=job_id).model_dump(), meta=meta)
