# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Manifest generation endpoints — async via job queue.

POST /api/manifest/generate → enqueues job, returns {job_id}
"""

from fastapi import APIRouter, Request

from app.schemas.common import ApiResponse, ApiMeta
from app.schemas.manifest import ManifestRequest
from app.schemas.job import JobCreateResponse
from app.jobs.store import create_job

router = APIRouter(prefix="/api/manifest", tags=["manifest"])


@router.post("/generate")
async def generate_manifest(
    body: ManifestRequest,
    request: Request,
):
    """Enqueue a MANIFEST.zip generation job.

    Returns a job_id for polling via GET /api/jobs/{id}.
    """
    job_id = await create_job()

    from app.jobs.runner import enqueue_local_job
    from app.jobs.definitions import generate_manifest_job
    enqueue_local_job(generate_manifest_job(job_id, body.model_dump()))

    return ApiResponse(
        data=JobCreateResponse(job_id=job_id).model_dump(),
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )
