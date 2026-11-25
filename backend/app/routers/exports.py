# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Data exports.

POST /api/exports/camtrapdp → starts the CamtrapDP export job (#328), returns {job_id}.
Poll it with GET /api/jobs/{id}; its result_url is the signed download link.
Gated by ``FF_CAMTRAPDP_EXPORT_ENABLED``, and needs a configured object store for the ZIPs
(``settings.object_store_configured``): without either, the endpoint answers FEATURE_DISABLED and the website falls back to the
metadata-only package.

GET /api/exports/camtrapdp/{job_id}/download → that link on S3 once EXPORT_LINK_SECRET is set (#462),
opened without a sign-in: a redirect to a fresh presigned GET of the ZIP.
"""

from dataclasses import asdict

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from app.authz import assert_access
from app.config import settings
from app.dependencies import get_current_user
from app.domain.camtrapdp_export import ExportCaller, ExportSelection, open_signed_link
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
    if not settings.camtrapdp_export_enabled:
        message = "CamtrapDP export is disabled (FF_CAMTRAPDP_EXPORT_ENABLED, or no object store for its ZIPs)."
        return ApiResponse(error=ApiError(code="FEATURE_DISABLED", message=message), meta=meta)

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
    await enqueue_job("export_camtrapdp_originals_job", job_id, asdict(selection), asdict(caller), link_base=str(request.base_url))
    return ApiResponse(data=JobCreateResponse(job_id=job_id).model_dump(), meta=meta)


@router.get("/camtrapdp/{job_id}/download")
async def download_camtrapdp_export(job_id: str, name: str = Query(""), expires: str = Query(""), sig: str = Query("")):
    """Open an export's signed link: a 307 to a fresh presigned GET of its ZIP, every time.

    No sign-in, since the link is opened from a new tab, an email or an API client: its HMAC
    is the authorisation (``domain/camtrapdp_export.py``). 404 unless the server signs export
    links (``OBJECT_STORE_BACKEND=s3`` and ``EXPORT_LINK_SECRET`` set); 403 for an expired,
    altered or malformed link, with no reason given.
    """
    if not settings.export_link_signed:
        raise HTTPException(status_code=404, detail="Not Found")
    url = open_signed_link(job_id, name, expires, sig)
    if url is None:
        raise HTTPException(status_code=403, detail="This download link is not valid or has expired.")
    return RedirectResponse(url, status_code=307, headers={"Cache-Control": "no-store"})
