# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Manifest generation endpoints — async via job queue.

POST /api/manifest/generate → enqueues job, returns {job_id}
GET  /api/manifest/branches → list available firmware branches
"""

from fastapi import APIRouter, Request

from app.jobs.store import create_job
from app.schemas.common import ApiMeta, ApiResponse
from app.schemas.job import JobCreateResponse
from app.schemas.manifest import ManifestRequest

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

    from app.jobs.definitions import generate_manifest_job
    from app.jobs.runner import enqueue_local_job

    enqueue_local_job(generate_manifest_job(job_id, body.model_dump()))

    return ApiResponse(
        data=JobCreateResponse(job_id=job_id).model_dump(),
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


@router.get("/branches")
async def get_firmware_branches(request: Request):
    """Return available branches from the Grove Vision AI firmware repo.

    Proxied through the backend to avoid CORS issues on the frontend.
    """
    from app.domain.manifest import fetch_github_branches

    branches = await fetch_github_branches()

    return ApiResponse(
        data=branches,
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )
