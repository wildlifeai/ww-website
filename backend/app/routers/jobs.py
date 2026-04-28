# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Job status polling endpoints.

GET /api/jobs/{id}       → current status + progress
GET /api/jobs/{id}/result → download or signed URL (when completed)
"""

from fastapi import APIRouter, HTTPException, Request

from app.jobs.store import get_job
from app.schemas.common import ApiMeta, ApiResponse

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/{job_id}")
async def get_job_status(job_id: str, request: Request):
    """Poll the current status of an async job."""
    job = await get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return ApiResponse(
        data=job.model_dump(),
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


@router.get("/{job_id}/result")
async def get_job_result(job_id: str, request: Request):
    """Get the result of a completed job (download URL or streamed file)."""
    job = await get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status.value not in ("completed", "completed_with_errors"):
        raise HTTPException(
            status_code=409,
            detail=f"Job is not completed (current status: {job.status.value})",
        )

    if not job.result_url:
        raise HTTPException(
            status_code=404,
            detail="Job completed but result is no longer available",
        )

    return ApiResponse(
        data={"result_url": job.result_url},
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )
