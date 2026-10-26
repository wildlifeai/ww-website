# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Job status polling endpoints.

GET /api/jobs            → the authenticated user's jobs, filtered and paged (processing history)
GET /api/jobs/{id}       → current status + progress
GET /api/jobs/{id}/result → download or signed URL (when completed)
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.dependencies import get_current_user
from app.jobs.store import JOB_CURSOR_PATTERN, get_job, list_jobs
from app.schemas.common import ApiMeta, ApiResponse
from app.schemas.job import JobStatus

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
async def list_user_jobs(
    request: Request,
    user=Depends(get_current_user),
    limit: int = Query(50, ge=1, le=200),
    status: Optional[List[JobStatus]] = Query(None, description="Only jobs in these statuses; repeat the parameter for several"),
    kind: Optional[str] = Query(None, pattern=r"^[a-z_]{1,40}$", description="Only jobs of this kind, such as upload or ai_pipeline"),
    before: Optional[str] = Query(None, pattern=JOB_CURSOR_PATTERN, description="The previous page's next_before"),
):
    """List the authenticated user's processing jobs, newest first, a page at a time.

    Powers the avatar-menu "Processing history" view so a user can see whether a
    past upload / AI run finished or failed after the live dock has gone away.
    Scoped to the caller: only jobs stamped with their ``user_id`` are returned.
    ``next_before`` reads the next page and is null on the last one.
    """
    jobs, next_before = await list_jobs(user.id, limit=limit, statuses=[s.value for s in status or []], kind=kind, before=before)
    return ApiResponse(
        data={"jobs": jobs, "next_before": next_before},
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


def _authorize_job(job, user) -> None:
    """404 if the job isn't the caller's. Hidden (not 403) so job ids can't be probed.

    Owner-less jobs (legacy / system jobs that carry no ``user_id``) are readable by
    any authenticated user; per-user jobs are scoped to the owner. A job an
    organisation's API key started is read with that organisation's keys
    (``GET /api/v1/jobs/{id}``), never here: its result is that organisation's data.
    """
    if (job.user_id and job.user_id != user.id) or (not job.user_id and job.organisation_id):
        raise HTTPException(status_code=404, detail="Job not found")


@router.get("/{job_id}")
async def get_job_status(job_id: str, request: Request, user=Depends(get_current_user)):
    """Poll the current status of an async job (scoped to the caller)."""
    job = await get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _authorize_job(job, user)

    return ApiResponse(
        data=job.model_dump(),
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


@router.get("/{job_id}/result")
async def get_job_result(job_id: str, request: Request, user=Depends(get_current_user)):
    """Get the result of a completed job (download URL or streamed file)."""
    job = await get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _authorize_job(job, user)

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
