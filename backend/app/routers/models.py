# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Model conversion and upload endpoints — async via job queue.

POST /api/models/convert  → validates + stores blob + enqueues conversion job
POST /api/models/upload   → enqueues upload + registration job
GET  /api/models/sscma/catalog → cached SSCMA model list (sync)
POST /api/models/pretrained → download + package GitHub model (async)
"""

from fastapi import APIRouter, UploadFile, File, HTTPException, Request, Depends

from app.schemas.common import ApiResponse, ApiMeta
from app.schemas.job import JobCreateResponse
from app.jobs.store import create_job
from app.dependencies import get_current_user
from app.services.blob_store import store_blob

router = APIRouter(prefix="/api/models", tags=["models"])

MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB
ALLOWED_MIME_TYPES = {"application/zip", "application/x-zip-compressed"}


@router.post("/convert")
async def convert_model(
    file: UploadFile = File(...),
    request: Request = None,
    user=Depends(get_current_user),
):
    """Upload a ZIP and enqueue Vela conversion job.

    The uploaded file is stored in Redis as a temp blob so the ARQ worker
    can retrieve it without shared filesystem access.
    """
    # Validate MIME type
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(400, detail=f"Invalid file type: {file.content_type}")

    # Read and validate size
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            413, detail=f"File exceeds {MAX_UPLOAD_SIZE // 1024 // 1024}MB limit"
        )

    # ZIP magic bytes check
    if not content[:4] == b"PK\x03\x04":
        raise HTTPException(400, detail="File is not a valid ZIP archive")

    job_id = await create_job()

    # Store the uploaded file in Redis for the worker to retrieve
    await store_blob(
        job_id,
        content,
        metadata={
            "filename": file.filename,
            "user_id": user.id,
            "content_type": file.content_type,
        },
    )

    # Enqueue via ARQ
    arq_pool = getattr(request.app.state, "arq_pool", None)
    if arq_pool:
        await arq_pool.enqueue_job(
            "convert_model",
            job_id=job_id,
            user_id=user.id,
        )

    return ApiResponse(
        data=JobCreateResponse(job_id=job_id).model_dump(),
        meta=ApiMeta(
            request_id=getattr(request.state, "request_id", None) if request else None
        ),
    )


from pydantic import BaseModel

class PretrainedModelRequest(BaseModel):
    sscma_uuid: str

@router.get("/sscma/catalog")
async def sscma_catalog(request: Request):
    """Return cached SSCMA model zoo catalog.

    Uses Redis cache with 1-hour TTL to avoid hitting GitHub on every request.
    """
    from app.services.sscma import get_sscma_catalog

    try:
        models = await get_sscma_catalog()
    except Exception:
        models = []

    return ApiResponse(
        data=models,
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


@router.post("/pretrained")
async def download_pretrained(
    body: PretrainedModelRequest,
    request: Request,
    user=Depends(get_current_user),
):
    """Download, package, and register an SSCMA pre-trained model via async job."""
    
    # We must retrieve the org ID the user is operating in.
    # For now, we query the first org admin/owner.
    from app.services.supabase_client import create_service_client
    
    client = create_service_client()
    roles = (
        client.table("user_roles")
        .select("organisation_id")
        .eq("user_id", user.id)
        .in_("role", ["admin", "owner", "member"])
        .execute()
    )

    if not roles.data:
        raise HTTPException(403, detail="User must belong to an organisation")

    org_id = roles.data[0]["organisation_id"]

    job_id = await create_job()

    # Enqueue via ARQ
    arq_pool = getattr(request.app.state, "arq_pool", None)
    if arq_pool:
        await arq_pool.enqueue_job(
            "download_pretrained",
            job_id=job_id,
            user_id=user.id,
            sscma_uuid=body.sscma_uuid,
            org_id=org_id,
        )

    return ApiResponse(
        data=JobCreateResponse(job_id=job_id).model_dump(),
        meta=ApiMeta(
            request_id=getattr(request.state, "request_id", None) if request else None
        ),
    )
