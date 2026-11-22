"""
CamtrapDP router, HTTP endpoints for importing CamtrapDP packages.

POST /api/camtrapdp/import
  Takes a CamtrapDP v1.0 ZIP, in the body or as the key of an S3 upload, and starts the
  import job (jobs/camtrapdp_import.py), which inserts the package into the Wildlife Watcher
  database. Answers {job_id}; follow it with GET /api/jobs/{id}.
"""

from typing import Optional

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.authz import project_creator_org_id
from app.config import settings
from app.dependencies import get_current_user, require_not_demo
from app.domain.camtrapdp import MAX_UPLOAD_BYTES
from app.jobs.dispatch import enqueue_job
from app.jobs.store import create_job, update_job
from app.schemas.common import ApiResponse
from app.schemas.job import JobCreateResponse, JobStatus
from app.services.incoming import IncomingError, filename_of, incoming_key, keep_for_job

logger = structlog.get_logger()

router = APIRouter(prefix="/api/camtrapdp", tags=["camtrapdp"])

TOO_BIG = f"File exceeds the {MAX_UPLOAD_BYTES // 1_048_576} MB limit."


@router.post("/import", response_model=ApiResponse, dependencies=[Depends(require_not_demo)])
async def import_camtrapdp(
    file: Optional[UploadFile] = File(None),
    file_key: Optional[str] = Form(None),
    annotation_mode: str = Form("final"),
    run_ai: bool = Form(False),
    user=Depends(get_current_user),
):
    """
    Start importing a CamtrapDP v1.0 ZIP package; answers ``{job_id}``.

    The job creates a new project, placeholder devices for each unique cameraID,
    and inserts all deployments, media, and observations from the package. Once it
    is complete, ``GET /api/jobs/{job_id}`` carries the counts, the warnings and any
    ``ai_job_id`` in ``result``.

    The authenticated user becomes project admin of the new project.
    Placeholder devices are named '[imported] <cameraID>'; a re-import by
    the same organisation reuses them.
    """
    # The package in the body, or as a key in S3 (services/incoming.py).
    if file is not None and not file_key:
        filename = file.filename or ""
    elif file_key and file is None:
        filename = filename_of(file_key)
    else:
        raise HTTPException(status_code=400, detail="Send one .zip file.")
    if not filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip files are accepted.")

    if annotation_mode not in ("final", "unprocessed"):
        annotation_mode = "final"

    # The import creates a project, so the rule is project creation's: an active
    # organisation_member or organisation_manager role (ww-backend's projects INSERT policy).
    org_id = await project_creator_org_id(user.id)
    if not org_id:
        raise HTTPException(status_code=403, detail="You must belong to an organisation to import data.")

    # The job reads the package from the object store: where the browser put it, or a copy of the body.
    if file is not None:
        if file.size and file.size > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=TOO_BIG)
        zip_bytes = await file.read()
        if len(zip_bytes) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=TOO_BIG)
        if not settings.object_store_configured:
            raise HTTPException(status_code=503, detail="This server has nowhere to keep the package for the import. Try again later.")
        package_key = await keep_for_job(user.id, zip_bytes)
    else:
        package_key = str(file_key)
        try:
            await incoming_key(user.id, package_key)
        except IncomingError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    job_id = await create_job(user_id=user.id, kind="camtrapdp_import", label=f"CamtrapDP import, {filename[:80]}")
    try:
        await enqueue_job("import_camtrapdp_job", job_id, package_key, filename, user.id, org_id, annotation_mode, run_ai)
    except Exception as exc:
        logger.error("camtrapdp_import_enqueue_failed", job_id=job_id, error=str(exc))
        await update_job(job_id, status=JobStatus.FAILED, error="The import could not be started. Try again.")
        raise HTTPException(status_code=503, detail="The import could not be started. Try again.") from exc
    logger.info("camtrapdp_import_queued", job_id=job_id, user_id=user.id, org_id=org_id, filename=filename)
    return ApiResponse(data=JobCreateResponse(job_id=job_id).model_dump())
