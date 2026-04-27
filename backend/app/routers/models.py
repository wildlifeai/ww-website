# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Model conversion and upload endpoints — async via job queue.

POST /api/models/convert  → validates + stores blob + enqueues conversion job
POST /api/models/upload   → enqueues upload + registration job
GET  /api/models/sscma/catalog → cached SSCMA model list (sync)
POST /api/models/pretrained → download + package GitHub model (async)
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from app.dependencies import get_current_user
from app.jobs.store import create_job
from app.schemas.common import ApiMeta, ApiResponse
from app.schemas.job import JobCreateResponse
from app.services.blob_store import store_blob

router = APIRouter(prefix="/api/models", tags=["models"])

MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB
ALLOWED_MIME_TYPES = {
    "application/zip",
    "application/x-zip-compressed",
    "application/octet-stream", # For raw .tflite or .cc
    "text/x-c", # For .cc
    "text/plain"
}

@router.post("/convert")
async def convert_model(
    file: UploadFile = File(...),
    model_name: str = Form(...),
    description: str = Form(""),
    organisation_id: str = Form(""),
    request: Request = None,
    user=Depends(get_current_user),
):
    """Upload a model and enqueue conversion/registration job.

    1. Validates input
    2. Determines user's organisation via user_roles.scope_id
    3. Auto-versions the model by name within the org
    4. Inserts an ai_models row
    5. Stores the file in blob store and enqueues the worker job
    """
    if file.content_type not in ALLOWED_MIME_TYPES and not file.filename.endswith(('.zip', '.tflite', '.cc')):
        raise HTTPException(400, detail=f"Invalid file type: {file.content_type}")

    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            413, detail=f"File exceeds {MAX_UPLOAD_SIZE // 1024 // 1024}MB limit"
        )

    from app.services.supabase_client import create_service_client
    client = create_service_client()

    # Get org_id for the user (scope_type='organisation' with org-level roles)
    roles = (
        client.table("user_roles")
        .select("scope_id, role")
        .eq("user_id", user.id)
        .eq("scope_type", "organisation")
        .eq("is_active", True)
        .is_("deleted_at", "null")
        .execute()
    )
    if not roles.data:
        raise HTTPException(403, detail="User must belong to an organisation")

    manager_roles = [r for r in roles.data if r.get("role") == "organisation_manager"]
    if not manager_roles:
        raise HTTPException(403, detail="Only organisation managers can upload models.")

    # Use frontend-supplied org_id if provided, otherwise pick first
    if organisation_id:
        valid = any(r["scope_id"] == organisation_id for r in manager_roles)
        if not valid:
            raise HTTPException(403, detail="You are not a manager of the selected organisation.")
        org_id = organisation_id
    else:
        org_id = manager_roles[0]["scope_id"]

    job_id = await create_job()

    # Get existing models with this name to determine version
    existing_res = client.table("ai_models").select("version").eq("organisation_id", org_id).eq("name", model_name).execute()
    existing_versions = [
        int(r["version"].split(".")[0])
        for r in existing_res.data
        if r.get("version") and "." in r["version"] and r["version"].split(".")[0].isdigit()
    ]
    next_ver = max(existing_versions) + 1 if existing_versions else 1
    version_string = f"{next_ver}.0.0"

    # We need a unique storage_path. We will update it after upload in the worker, but for now use a placeholder.
    temp_storage_path = f"temp/{org_id}/{model_name.replace(' ', '_')}_{version_string}_{job_id}"

    # Insert ai_models row
    model_row = client.table("ai_models").insert({
        "organisation_id": org_id,
        "version": version_string,
        "name": model_name,
        "description": description,
        "uploaded_by": user.id,
        "modified_by": user.id,
        "storage_path": temp_storage_path,
        "file_type": "uploading"
    }).execute()
    model_id = model_row.data[0]["id"]

    await store_blob(
        job_id,
        content,
        metadata={
            "filename": file.filename,
            "user_id": user.id,
            "content_type": file.content_type,
        },
    )

    from app.jobs.definitions import convert_model_job
    from app.jobs.runner import enqueue_local_job
    enqueue_local_job(convert_model_job(job_id, user.id, model_id))

    return ApiResponse(
        data={
            "job_id": job_id,
            "model_id": model_id,
            "status": "uploading",
            "poll_url": f"/api/jobs/{job_id}",
        },
        meta=ApiMeta(
            request_id=getattr(request.state, "request_id", None) if request else None,
            message="Model upload started. Poll the job URL for progress.",
        ),
    )





class PretrainedModelRequest(BaseModel):
    source_type: str = "sscma" # "sscma" or "pretrained"
    sscma_uuid: str = "" # Used if source_type == "sscma"
    architecture: str = "" # Used if source_type == "pretrained"
    resolution: str = "" # Used if source_type == "pretrained"
    model_name: str = "" # Custom name if provided
    description: str = "" # Custom description if provided
    organisation_id: str = "" # Frontend-selected org


@router.get("/managed-orgs")
async def get_managed_orgs(
    request: Request,
    user=Depends(get_current_user),
):
    """Return organisations where the current user is an organisation_manager."""
    import structlog

    from app.services.supabase_client import create_service_client
    logger = structlog.get_logger()
    client = create_service_client()

    roles = (
        client.table("user_roles")
        .select("scope_id, role")
        .eq("user_id", user.id)
        .eq("scope_type", "organisation")
        .eq("role", "organisation_manager")
        .eq("is_active", True)
        .is_("deleted_at", "null")
        .execute()
    )

    logger.info("managed_orgs_query", email=user.email, user_id=user.id, roles_count=len(roles.data) if roles.data else 0)

    if not roles.data:
        return ApiResponse(
            data=[],
            meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
        )

    # Fetch org names
    org_ids = [r["scope_id"] for r in roles.data]
    orgs = client.table("organisations").select("id, name").in_("id", org_ids).execute()

    return ApiResponse(
        data=orgs.data or [],
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )

@router.get("/sscma/catalog")
async def sscma_catalog(request: Request):
    """Return cached SSCMA model zoo catalog.

    Uses Redis cache with 1-hour TTL to avoid hitting GitHub on every request.
    """
    from app.services.sscma import get_sscma_catalog

    try:
        models = await get_sscma_catalog()
    except Exception as e:
        import structlog
        structlog.get_logger().error("sscma_catalog_failed", error=str(e))
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
    """Download, package, and register a pre-trained model (SSCMA or built-in)."""

    from app.services.supabase_client import create_service_client
    client = create_service_client()

    # Must be organisation_manager
    roles = (
        client.table("user_roles")
        .select("scope_id, role")
        .eq("user_id", user.id)
        .eq("scope_type", "organisation")
        .eq("is_active", True)
        .is_("deleted_at", "null")
        .execute()
    )

    if not roles.data:
        raise HTTPException(403, detail="User must belong to an organisation")

    manager_roles = [r for r in roles.data if r.get("role") == "organisation_manager"]
    if not manager_roles:
        raise HTTPException(403, detail="Only organisation managers can import pre-trained models.")

    # Use frontend-supplied org_id if provided, otherwise pick first managed org
    if body.organisation_id:
        # Verify user actually manages this org
        valid = any(r["scope_id"] == body.organisation_id for r in manager_roles)
        if not valid:
            raise HTTPException(403, detail="You are not a manager of the selected organisation.")
        org_id = body.organisation_id
    else:
        org_id = manager_roles[0]["scope_id"]

    if body.source_type == "pretrained":
        if not body.architecture or not body.resolution:
            raise HTTPException(400, detail="Architecture and resolution are required for GitHub pre-trained models.")

        job_id = await create_job()

        from app.jobs.definitions import download_github_pretrained_job
        from app.jobs.runner import enqueue_local_job
        enqueue_local_job(download_github_pretrained_job(
            job_id, user.id, org_id, body.architecture, body.resolution, body.description
        ))

        return ApiResponse(
            data=JobCreateResponse(job_id=job_id).model_dump(),
            meta=ApiMeta(
                request_id=getattr(request.state, "request_id", None) if request else None
            ),
        )
    else:
        # SSCMA model
        if not body.sscma_uuid:
            raise HTTPException(400, detail="sscma_uuid is required for SenseCap models.")

        job_id = await create_job()

        from app.jobs.definitions import download_pretrained_job
        from app.jobs.runner import enqueue_local_job
        enqueue_local_job(download_pretrained_job(
            job_id, user.id, body.sscma_uuid, org_id, body.model_name, body.description
        ))

        return ApiResponse(
            data=JobCreateResponse(job_id=job_id).model_dump(),
            meta=ApiMeta(
                request_id=getattr(request.state, "request_id", None) if request else None
            ),
        )
