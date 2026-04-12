# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Model conversion and upload endpoints — async via job queue.

POST /api/models/convert  → enqueues conversion job
POST /api/models/upload   → enqueues upload + registration job
GET  /api/models/sscma/catalog → cached SSCMA model list (sync)
"""

from fastapi import APIRouter, UploadFile, File, HTTPException, Request, Depends

from app.schemas.common import ApiResponse, ApiMeta
from app.schemas.job import JobCreateResponse
from app.jobs.store import create_job
from app.dependencies import get_current_user

router = APIRouter(prefix="/api/models", tags=["models"])

MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB
ALLOWED_MIME_TYPES = {"application/zip", "application/x-zip-compressed"}


@router.post("/convert")
async def convert_model(
    file: UploadFile = File(...),
    request: Request = None,
    user=Depends(get_current_user),
):
    """Upload a ZIP and enqueue Vela conversion job."""
    # Validate
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(400, detail=f"Invalid file type: {file.content_type}")

    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(413, detail=f"File exceeds {MAX_UPLOAD_SIZE // 1024 // 1024}MB limit")

    # ZIP magic bytes check
    if not content[:4] == b"PK\x03\x04":
        raise HTTPException(400, detail="File is not a valid ZIP archive")

    job_id = await create_job()

    # TODO: Save file to temp storage and enqueue ARQ job
    # await request.app.state.arq_pool.enqueue_job(
    #     "convert_model", job_id=job_id, file_path="...", user_id=user.id, org_id="..."
    # )

    return ApiResponse(
        data=JobCreateResponse(job_id=job_id).model_dump(),
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None) if request else None),
    )


@router.get("/sscma/catalog")
async def sscma_catalog(request: Request):
    """Return cached SSCMA model zoo catalog.

    TODO: Wire up cache.cached() with SSCMA fetch function.
    """
    return ApiResponse(
        data=[],
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )
