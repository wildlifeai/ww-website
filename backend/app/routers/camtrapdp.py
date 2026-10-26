"""
CamtrapDP router — HTTP endpoints for importing CamtrapDP packages.

POST /api/camtrapdp/import
  Accepts a multipart ZIP upload, parses it as a CamtrapDP v1.0 package,
  and inserts all data into the Wildlife Watcher database.
"""

import asyncio

import structlog
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile

from app.authz import project_creator_org_id
from app.dependencies import get_current_user, get_privileged_client, get_user_client, require_not_demo
from app.domain import camtrapdp as domain
from app.schemas.camtrapdp import CamtrapImportResult
from app.schemas.common import ApiResponse

logger = structlog.get_logger()

router = APIRouter(prefix="/api/camtrapdp", tags=["camtrapdp"])

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


@router.post("/import", response_model=ApiResponse, dependencies=[Depends(require_not_demo)])
async def import_camtrapdp(
    file: UploadFile,
    annotation_mode: str = Form("final"),
    run_ai: bool = Form(False),
    user=Depends(get_current_user),
    user_client=Depends(get_user_client),
    svc=Depends(get_privileged_client),
):
    """
    Import a CamtrapDP v1.0 ZIP package.

    Creates a new project, placeholder devices for each unique cameraID,
    and inserts all deployments, media, and observations from the package.

    The authenticated user becomes project admin of the new project.
    Placeholder devices are named '[imported] <cameraID>'; a re-import by
    the same organisation reuses them.
    """
    # ── Validate upload ────────────────────────────────────────────────
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip files are accepted.")

    if annotation_mode not in ("final", "unprocessed"):
        annotation_mode = "final"

    if file.size and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {MAX_UPLOAD_BYTES // 1_048_576} MB limit.",
        )
    zip_bytes = await file.read()
    if len(zip_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {MAX_UPLOAD_BYTES // 1_048_576} MB limit.",
        )

    # ── Resolve user's primary organisation ───────────────────────────
    # The import creates a project, so the rule is project creation's: an active
    # organisation_member or organisation_manager role (ww-backend's projects INSERT policy).
    org_id = await project_creator_org_id(user.id)
    if not org_id:
        raise HTTPException(
            status_code=403,
            detail="You must belong to an organisation to import data.",
        )

    # ── Parse ZIP ──────────────────────────────────────────────────────
    try:
        pkg = await asyncio.to_thread(domain.parse_zip, zip_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Light validation (adds warnings, does not block)
    pkg.warnings.extend(domain.validate_package(pkg))

    logger.info(
        "camtrapdp_import_started",
        user_id=user.id,
        org_id=org_id,
        filename=file.filename,
        deployments=len(pkg.deployments),
        media=len(pkg.media),
        observations=len(pkg.observations),
    )

    # ── Register missing taxa ──────────────────────────────────────────
    unique_names = {obs.get("scientificName", "").strip() for obs in pkg.observations if obs.get("scientificName", "").strip()}

    if unique_names:

        def get_existing_taxa():
            return svc.table("taxa").select("scientific_name").in_("scientific_name", list(unique_names)).execute()

        try:
            existing_res = await asyncio.to_thread(get_existing_taxa)
            existing_names = {row["scientific_name"] for row in existing_res.data}
            missing_names = unique_names - existing_names

            from app.domain.inaturalist import search_and_fetch_inat_taxon

            for name in missing_names:
                try:
                    new_taxon = await search_and_fetch_inat_taxon(name)
                    if new_taxon:
                        resolved_name = new_taxon.get("scientific_name", "")

                        # iNat may resolve a synonym to a different accepted name that
                        # already exists in the DB (e.g. Anas strepera → Mareca strepera).
                        # Re-check with the resolved name to avoid a duplicate key error.
                        def check_resolved(rn=resolved_name):
                            return svc.table("taxa").select("scientific_name").eq("scientific_name", rn).limit(1).execute()

                        resolved_res = await asyncio.to_thread(check_resolved)
                        if resolved_res.data:
                            logger.info(
                                "camtrapdp_import_taxon_already_exists_as_synonym",
                                queried=name,
                                resolved=resolved_name,
                            )
                        else:

                            def insert_taxon(t=new_taxon):
                                return svc.table("taxa").insert(t).execute()

                            await asyncio.to_thread(insert_taxon)
                            logger.info(
                                "camtrapdp_import_registered_taxon",
                                name=name,
                                resolved=resolved_name,
                                inat_id=new_taxon.get("inat_taxon_id"),
                            )
                    else:
                        logger.warning("camtrapdp_import_taxon_not_found_on_inat", name=name)
                        pkg.warnings.append(f"Could not find a valid iNaturalist taxon for '{name}'.")
                except Exception as e:
                    logger.error("camtrapdp_import_taxon_insert_failed", name=name, error=str(e))
                    pkg.warnings.append(f"Failed to register taxon '{name}': {e}")
        except Exception as e:
            logger.error("camtrapdp_import_taxa_check_failed", error=str(e))
            pkg.warnings.append(f"Failed to verify existing taxa: {e}")

    # ── Run import (blocking I/O — run in thread) ──────────────────────
    try:
        result: CamtrapImportResult = await asyncio.to_thread(domain.import_package, pkg, user.id, org_id, svc, user_client, annotation_mode)
    except Exception as exc:
        logger.error("camtrapdp_import_failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail=f"Import failed: {exc}",
        ) from exc

    # ── Google Drive upload for zip-embedded images ────────────────────
    # Files with a public URL are already stored as-is. Only images that
    # were physically inside the zip need uploading to Drive.
    if result.pending_drive_uploads:
        from app.config import settings as app_settings

        if not app_settings.GOOGLE_DRIVE_ENABLED:
            result.warnings.append(
                f"{len(result.pending_drive_uploads)} image(s) found in zip but "
                "Google Drive upload is disabled (GOOGLE_DRIVE_ENABLED=false). "
                "Files stored with their original relative paths."
            )
        else:
            try:
                from app.services.google_drive import (
                    GoogleDriveService,
                    build_deployment_folder_name,
                    sanitize_filename,
                    slugify,
                )

                drive = GoogleDriveService()

                # Build the file list in the format upload_analysis_images expects
                drive_files = []
                for p in result.pending_drive_uploads:
                    drive_files.append(
                        {
                            "file_bytes": p.file_bytes,
                            "filename": p.filename,
                            "media_id": p.media_id,
                            "timestamp": p.deployment_start,
                            "mime_type": p.mime_type,
                            "project": {"id": p.project_id, "name": p.project_name},
                            "deployment": {
                                "id": p.deployment_id,
                                "deployment_start": p.deployment_start,
                                "deployment_end": p.deployment_end,
                                "location_name": p.location_name or "",
                            },
                            "_project_folder": f"{slugify(p.project_name)}_{p.project_id[:8]}",
                            "_deployment_folder": build_deployment_folder_name(p.deployment_start, p.deployment_end, p.deployment_id),
                            "drive_filename": sanitize_filename(p.deployment_start, p.filename),
                        }
                    )

                stats = await drive.upload_analysis_images(drive_files)

                # ── Patch media.file_path → gdrive://<id> ──────────────────
                # Without this, zip-embedded images keep their relative path and
                # the resolver can't serve a thumbnail (issue: blank thumbnails).
                uploaded_files = stats.pop("files", [])
                patched = 0
                for uf in uploaded_files:
                    if not uf.get("media_id"):
                        continue  # only the CamtrapDP path pre-creates media to patch
                    try:
                        svc.table("media").update({"file_path": f"gdrive://{uf['file_id']}", "file_public": False}).eq("id", uf["media_id"]).execute()
                        patched += 1
                    except Exception as exc:
                        logger.warning(
                            "camtrapdp_media_path_patch_failed",
                            media_id=uf.get("media_id"),
                            error=str(exc),
                        )
                if patched:
                    logger.info("camtrapdp_media_paths_patched", count=patched)

                result.drive_uploads = stats
                logger.info("camtrapdp_drive_upload_complete", **stats)

            except Exception as exc:
                logger.error("camtrapdp_drive_upload_failed", error=str(exc))
                result.warnings.append(f"Google Drive upload failed: {exc}")

    # ── Optional AI (opt-in): run SpeciesNet + Wildlife Brain on the imported,
    # image-backed deployments — i.e. the media whose files were physically in the
    # zip and are now archived to Drive (so resolve_media can fetch them). CamtrapDP
    # arrives already labelled, so this defaults off. No-op unless FF_ML_ENABLED.
    if run_ai and result.pending_drive_uploads:
        from app.config import settings  # noqa: PLC0415

        ai_dep_ids = sorted({p.deployment_id for p in result.pending_drive_uploads if p.deployment_id})
        # Only the media whose files were physically in the zip — NOT the fileless CSV
        # references. Scoping the pipeline to these avoids media_prep choking on hundreds
        # of unresolvable rows (which blocked the event loop → ARQ cancel + KEDA scaledown).
        ai_media_ids = sorted({p.media_id for p in result.pending_drive_uploads if p.media_id})
        if settings.FF_ML_ENABLED and ai_dep_ids:
            from app.jobs.dispatch import enqueue_job  # noqa: PLC0415
            from app.jobs.store import create_job  # noqa: PLC0415

            ai_job_id = await create_job(
                user_id=user.id,
                kind="ai_pipeline",
                label=f"AI analysis — {len(ai_media_ids)} imported image(s)",
                deployment_ids=ai_dep_ids,
            )
            # force=True: imported CamtrapDP labels (machine → source_type='ai') must not make
            # run_pipeline skip these media. media_ids scopes it to the image-backed rows only.
            await enqueue_job("annotate_deployments_job", ai_job_id, ai_dep_ids, user.id, True, ai_media_ids)
            result.ai_job_id = ai_job_id
            logger.info("camtrapdp_ai_enqueued", job_id=ai_job_id, deployments=len(ai_dep_ids))

    # Strip raw bytes before serialising the response
    result.pending_drive_uploads = []

    return ApiResponse(data=result.model_dump())
