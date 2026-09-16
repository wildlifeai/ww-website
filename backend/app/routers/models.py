# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Model conversion and upload endpoints — async via job queue.

POST /api/models/convert  → validates + stores blob + enqueues conversion job
POST /api/models/upload   → enqueues upload + registration job
GET  /api/models/sscma/catalog → cached SSCMA model list (sync)
POST /api/models/pretrained → download + package GitHub model (async)
GET  /api/models/train/status → is training on, which trainer, dataset limits (sync)
POST /api/models/train    → train a Species Brain from an Annotations selection (async)
"""

import asyncio
import uuid

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from app.authz import accessible_deployment_ids
from app.config import settings
from app.dependencies import get_current_user, get_manager_roles, get_verified_user
from app.domain.model import next_model_version, resolve_or_create_model_family
from app.domain.training import media_deployments, training_mode, training_status
from app.jobs.definitions import convert_model_job, download_github_pretrained_job, download_pretrained_job
from app.jobs.dispatch import enqueue_job
from app.jobs.runner import enqueue_local_job
from app.jobs.store import create_job
from app.middleware.rate_limit import limiter
from app.schemas.common import ApiError, ApiMeta, ApiResponse
from app.schemas.job import JobCreateResponse
from app.schemas.model import TrainModelRequest
from app.services.blob_store import store_blob
from app.services.sscma import get_sscma_catalog
from app.services.supabase_client import create_service_client

router = APIRouter(prefix="/api/models", tags=["models"])

MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB
ALLOWED_MIME_TYPES = {
    "application/zip",
    "application/x-zip-compressed",
    "application/octet-stream",  # For raw .tflite or .cc
    "text/x-c",  # For .cc
    "text/plain",
}


def resolve_managed_org(requested_org_id: str | None, manager_roles: list) -> str:
    if not manager_roles:
        raise HTTPException(403, detail="Only organisation managers can perform this action.")
    if requested_org_id:
        if not any(r["scope_id"] == requested_org_id for r in manager_roles):
            raise HTTPException(403, detail="You are not a manager of the selected organisation.")
        return requested_org_id
    if len(manager_roles) > 1:
        raise HTTPException(400, detail="You manage multiple organisations. Please explicitly provide organisation_id.")
    return manager_roles[0]["scope_id"]


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
    if file.content_type not in ALLOWED_MIME_TYPES and not file.filename.endswith((".zip", ".tflite", ".cc")):
        raise HTTPException(400, detail=f"Invalid file type: {file.content_type}")

    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(413, detail=f"File exceeds {MAX_UPLOAD_SIZE // 1024 // 1024}MB limit")

    client = create_service_client()

    manager_roles = await get_manager_roles(user)
    org_id = resolve_managed_org(organisation_id, manager_roles)

    job_id = await create_job(user_id=user.id, kind="model_upload", label=f"Upload model {model_name}")

    # Resolve or create AI Model Family (shared domain helper)
    model_family_id, _ = await resolve_or_create_model_family(client, org_id, model_name)

    # Get existing models with this name to determine version
    existing_query = client.table("ai_models").select("version").eq("organisation_id", org_id).eq("name", model_name)
    existing_res = await asyncio.to_thread(existing_query.execute)
    existing_versions = []
    for r in existing_res.data:
        v = r.get("version")
        if v:
            parts = v.split(".")
            if parts[0].isdigit():
                existing_versions.append(int(parts[0]))
    next_ver = max(existing_versions) + 1 if existing_versions else 1
    version_string = f"{next_ver}.0.0-{uuid.uuid4().hex[:6]}"

    # Insert ai_models row. model_path / labels_path are nullable (and UNIQUE), so
    # an unconverted model carries NULL until the worker writes the real storage
    # paths on success. (Postgres allows many NULLs under UNIQUE, so concurrent
    # pending uploads don't collide — this replaced an earlier "_pending/{job_id}"
    # placeholder hack once the NOT NULL constraint was dropped backend-side.)
    model_insert = (
        client.table("ai_models")
        .insert(
            {
                "organisation_id": org_id,
                "model_family_id": model_family_id,
                "version": version_string,
                "version_number": next_ver,
                "name": model_name,
                "description": description,
                "uploaded_by": user.id,
                "modified_by": user.id,
                "file_type": "uploading",
                "model_path": None,
                "labels_path": None,
            }
        )
        # supabase-py returns the inserted row by default; .select("id") makes
        # that explicit (and version-independent) since we read model_row.data[0]["id"].
        .select("id")
    )
    model_row = await asyncio.to_thread(model_insert.execute)
    if not model_row.data:
        raise HTTPException(500, detail="Failed to create AI model record")
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
    source_type: str = "sscma"  # "sscma" or "pretrained"
    sscma_uuid: str = ""  # Used if source_type == "sscma"
    architecture: str = ""  # Used if source_type == "pretrained"
    resolution: str = ""  # Used if source_type == "pretrained"
    model_name: str = ""  # Custom name if provided
    description: str = ""  # Custom description if provided
    organisation_id: str = ""  # Frontend-selected org


@router.get("/managed-orgs")
async def get_managed_orgs(
    request: Request,
    user=Depends(get_current_user),
):
    """Return organisations where the current user is an organisation_manager."""

    logger = structlog.get_logger()
    client = create_service_client()

    manager_roles = await get_manager_roles(user)

    logger.info("managed_orgs_query", email=user.email, user_id=user.id, roles_count=len(manager_roles))

    if not manager_roles:
        return ApiResponse(
            data=[],
            meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
        )

    # Fetch org names

    org_ids = [r["scope_id"] for r in manager_roles]
    orgs_query = client.table("organisations").select("id, name").in_("id", org_ids)
    orgs = await asyncio.to_thread(orgs_query.execute)

    return ApiResponse(
        data=orgs.data or [],
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


@router.get("/sscma/catalog")
async def sscma_catalog(request: Request):
    """Return cached SSCMA model zoo catalog.

    Uses Redis cache with 1-hour TTL to avoid hitting GitHub on every request.
    """

    try:
        models = await get_sscma_catalog()
    except Exception as e:
        structlog.get_logger().error("sscma_catalog_failed", error=str(e))
        models = []

    return ApiResponse(
        data=models,
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


@router.get("/pretrained/catalog")
async def pretrained_catalog(request: Request):
    """Return the built-in pretrained models the device can run.

    Allows the frontend to dynamically render the architecture/resolution
    dropdowns without hardcoding the model list. Registry entries marked
    ``blocked`` (the detection models, see ``model_registry.py``) are omitted so
    they cannot be selected.
    """
    from app.registries.model_registry import supported_models

    catalog = []
    for arch_name, arch_data in supported_models().items():
        catalog.append(
            {
                "architecture": arch_name,
                "firmware_model_id": arch_data.get("firmware_model_id"),
                "resolutions": list(arch_data.get("resolutions", {}).keys()),
                "labels": arch_data.get("labels", []),
            }
        )

    return ApiResponse(
        data=catalog,
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


@router.post("/pretrained")
async def download_pretrained(
    body: PretrainedModelRequest,
    request: Request,
    user=Depends(get_current_user),
):
    """Download, package, and register a pre-trained model (SSCMA or built-in)."""

    manager_roles = await get_manager_roles(user)
    org_id = resolve_managed_org(body.organisation_id, manager_roles)

    if body.source_type == "pretrained":
        if not body.architecture or not body.resolution:
            raise HTTPException(400, detail="Architecture and resolution are required for GitHub pre-trained models.")

        job_id = await create_job(user_id=user.id, kind="model_download", label=f"Download model {body.architecture} @ {body.resolution}")

        enqueue_local_job(download_github_pretrained_job(job_id, user.id, org_id, body.architecture, body.resolution, body.description))

        return ApiResponse(
            data=JobCreateResponse(job_id=job_id).model_dump(),
            meta=ApiMeta(request_id=getattr(request.state, "request_id", None) if request else None),
        )
    else:
        # SSCMA model
        if not body.sscma_uuid:
            raise HTTPException(400, detail="sscma_uuid is required for SenseCap models.")

        job_id = await create_job(user_id=user.id, kind="model_download", label=f"Download SenseCap model {body.model_name or body.sscma_uuid}")

        enqueue_local_job(download_pretrained_job(job_id, user.id, body.sscma_uuid, org_id, body.model_name, body.description))

        return ApiResponse(
            data=JobCreateResponse(job_id=job_id).model_dump(),
            meta=ApiMeta(request_id=getattr(request.state, "request_id", None) if request else None),
        )


# ── Species Brain training (Annotations → Actions → "Create species ID model…") ──────


def _training_disabled(req_id):
    return ApiResponse(
        error=ApiError(code="FEATURE_DISABLED", message="Model training is disabled (FF_MODEL_TRAINING_ENABLED)."),
        meta=ApiMeta(request_id=req_id),
    )


@router.get("/train/status")
async def train_status(request: Request, user=Depends(get_current_user)):
    """What the Annotations action needs before it opens: is training on, which trainer
    runs it (``edge_impulse`` or ``export_only``), and the dataset limits to validate against."""
    return ApiResponse(data=training_status(), meta=ApiMeta(request_id=getattr(request.state, "request_id", None)))


@router.post("/train")
@limiter.limit("10/minute")
async def train_model(request: Request, body: TrainModelRequest, user=Depends(get_verified_user)):
    """Train a Species Brain from an Annotations selection (async; ``train_species_brain_job``).

    Checks, in order: the feature flag; that the caller manages the organisation that
    will own the model; that every selected image still exists and sits in a deployment
    the caller may read. With a trainer configured an ``ai_models`` row is created up
    front (status ``uploading`` → ``validated`` | ``failed``, like an upload); without
    one the job only packages the dataset. Poll ``GET /api/jobs/{job_id}``.
    """
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_MODEL_TRAINING_ENABLED:
        return _training_disabled(req_id)

    manager_roles = await get_manager_roles(user)
    org_id = resolve_managed_org(body.organisation_id, manager_roles)

    media_ids = list(dict.fromkeys(body.media_ids))
    client = create_service_client()
    found = await media_deployments(client, media_ids)
    missing = len(media_ids) - len(found)
    if missing:
        raise HTTPException(400, detail=f"{missing} of the selected images no longer exist. Refresh the grid and select again.")
    deployment_ids = sorted(set(found.values()))
    allowed = set(await accessible_deployment_ids(user.id, deployment_ids))
    if any(d not in allowed for d in deployment_ids):
        raise HTTPException(403, detail="You do not have access to every selected image.")

    mode = training_mode()
    model_id = None
    if mode == "edge_impulse":
        model_family_id, _ = await resolve_or_create_model_family(client, org_id, body.model_name)
        next_ver, version_string = await next_model_version(client, org_id, body.model_name)
        model_insert = (
            client.table("ai_models")
            .insert(
                {
                    "organisation_id": org_id,
                    "model_family_id": model_family_id,
                    "version": version_string,
                    "version_number": next_ver,
                    "name": body.model_name,
                    "description": body.description or f"Trained on the website from {len(media_ids)} annotated images",
                    "uploaded_by": user.id,
                    "modified_by": user.id,
                    "file_type": "training",
                    "model_path": None,
                    "labels_path": None,
                }
            )
            .select("id")
        )
        model_row = await asyncio.to_thread(model_insert.execute)
        if not model_row.data:
            raise HTTPException(500, detail="Failed to create the AI model record")
        model_id = model_row.data[0]["id"]

    job_id = await create_job(user_id=user.id, kind="model_train", label=f"Train {body.model_name}")
    params = body.model_dump()
    params["media_ids"] = media_ids
    params["organisation_id"] = org_id
    await enqueue_job("train_species_brain_job", job_id, user.id, model_id, org_id, params)
    structlog.get_logger().info("train_model_queued", job_id=job_id, model_id=model_id, mode=mode, images=len(media_ids), org_id=org_id)

    return ApiResponse(
        data={"job_id": job_id, "model_id": model_id, "mode": mode, "status": "queued", "poll_url": f"/api/jobs/{job_id}"},
        meta=ApiMeta(request_id=req_id),
    )
