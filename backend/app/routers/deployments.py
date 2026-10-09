import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException
from postgrest.exceptions import APIError
from pydantic import BaseModel, Field, field_validator, model_validator

from app.authz import assert_access, assert_project_writer, deployment_id_prefix_bounds
from app.dependencies import get_current_user, get_user_client, get_verified_user, require_not_demo
from app.domain.deployment_location import apply_location_update, location_update
from app.domain.soft_delete import now_iso, restore_deployments, soft_delete_deployments
from app.schemas.common import ApiResponse
from app.services.supabase_client import create_service_client

logger = structlog.get_logger()

router = APIRouter(prefix="/api/deployments", tags=["deployments"])


class ValidateDeploymentsRequest(BaseModel):
    deployment_ids: List[str]


class CreateDeploymentRequest(BaseModel):
    project_id: str
    name: str
    # The deployment id to create under, when the caller already holds one: the
    # camera stamps the configured deployment's UUID into every frame (EXIF 0xF200),
    # and the phone that configured it holds the same id locally. Creating under that
    # id means a later sync from the phone converges on this row (push_changes is
    # ON CONFLICT DO NOTHING) instead of producing a second deployment (ww-website#140).
    id: Optional[str] = None
    location_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    deployment_start: Optional[str] = None  # ISO 8601; defaults to now
    deployment_end: Optional[str] = None


@router.post("")
async def create_deployment(
    body: CreateDeploymentRequest,
    user: Any = Depends(get_verified_user),
) -> Dict[str, Any]:
    """Create a deployment in a project the caller can access (manual upload assignment).

    Guarded by project access — a caller with no role on the project gets 404 (so other tenants'
    project IDs can't be probed). Auto-creates a placeholder device because deployments require a
    non-null ``device_id`` (mirrors the CamtrapDP import); a fresh device id also satisfies the
    ``(deployment_start, device_id)`` unique index.
    """
    # Access guard — raises 404 unless the caller holds a role on this project's org/project.
    await assert_access(user.id, project_id=body.project_id)

    # A supplied id must be a UUID (the column is uuid; a bad value would be a 500 from
    # Postgres) and must not already exist (a duplicate is a 409, never a silent reuse of
    # someone else's row).
    supplied_id: Optional[str] = None
    if body.id:
        try:
            supplied_id = str(uuid.UUID(body.id))
        except ValueError:
            raise HTTPException(status_code=400, detail="id must be a UUID")

    from app.domain.photo_preprocessing import resolve_timezone

    def _create() -> Dict[str, Any]:
        svc = create_service_client()
        proj = svc.table("projects").select("id, organisation_id").eq("id", body.project_id).limit(1).execute()
        if not proj.data:
            raise HTTPException(status_code=404, detail="Project not found")
        org_id = proj.data[0].get("organisation_id")

        if supplied_id:
            existing = svc.table("deployments").select("id").eq("id", supplied_id).limit(1).execute()
            if existing.data:
                raise HTTPException(status_code=409, detail="A deployment with this id already exists")

        name = (body.name or "").strip() or "Manual deployment"
        has_coords = body.latitude is not None and body.longitude is not None

        device_id = str(uuid.uuid4())
        svc.table("devices").insert(
            {
                "id": device_id,
                "bluetooth_id": str(uuid.uuid4()),
                "name": f"[manual] {name}"[:100],
                "organisation_id": org_id,
                "modified_by": user.id,
            }
        ).execute()

        start = body.deployment_start or datetime.now(timezone.utc).isoformat()
        row = {
            "id": supplied_id or str(uuid.uuid4()),
            "project_id": body.project_id,
            "device_id": device_id,
            "setup_by": user.id,
            "name": name,
            "location_name": body.location_name or name,
            "deployment_start": start,
            "deployment_end": body.deployment_end,
            "latitude": body.latitude,
            "longitude": body.longitude,
            "timezone": resolve_timezone(body.latitude, body.longitude) if has_coords else None,
        }
        row = {k: v for k, v in row.items() if v is not None}
        svc.table("deployments").insert(row).execute()
        return {
            "id": row["id"],
            "project_id": body.project_id,
            "name": name,
            "location_name": row["location_name"],
            "deployment_start": start,
            "latitude": body.latitude,
            "longitude": body.longitude,
        }

    return await asyncio.to_thread(_create)


class BatchDeploymentIdsRequest(BaseModel):
    deployment_ids: List[str]


class RestoreDeploymentsRequest(BaseModel):
    deployment_ids: List[str]
    deleted_at: str


async def _resolve_deployment_projects(deployment_ids: List[str], only_active: bool) -> Dict[str, str]:
    """Map deployment id → project id (service client). only_active filters out already-deleted rows."""

    def _q() -> Dict[str, str]:
        svc = create_service_client()
        q = svc.table("deployments").select("id, project_id").in_("id", deployment_ids)
        if only_active:
            q = q.is_("deleted_at", "null")
        resp = q.execute()
        return {r["id"]: r["project_id"] for r in (resp.data or [])}

    return await asyncio.to_thread(_q)


@router.delete("/batch", dependencies=[Depends(require_not_demo)])
async def batch_delete_deployments(
    body: BatchDeploymentIdsRequest,
    user: Any = Depends(get_verified_user),
) -> Dict[str, Any]:
    """Soft-delete deployments (and cascade to their media + observations).

    Requires write access (project_admin/member, org-manager, or system) on **every** project the
    selected deployments belong to. Deployments the caller can't write are refused (404). Returns
    the shared ``deleted_at`` so the client can offer an Undo.
    """
    if not body.deployment_ids:
        return {"deleted_at": None, "deployment_ids": []}

    dep_to_project = await _resolve_deployment_projects(body.deployment_ids, only_active=True)
    if not dep_to_project:
        return {"deleted_at": None, "deployment_ids": []}
    for project_id in set(dep_to_project.values()):
        await assert_project_writer(user.id, project_id)

    ts = now_iso()
    found_ids = list(dep_to_project.keys())
    await asyncio.to_thread(lambda: soft_delete_deployments(create_service_client(), found_ids, ts))
    return {"deleted_at": ts, "deployment_ids": found_ids}


@router.post("/batch/restore", dependencies=[Depends(require_not_demo)])
async def batch_restore_deployments(
    body: RestoreDeploymentsRequest,
    user: Any = Depends(get_verified_user),
) -> Dict[str, Any]:
    """Undo a deployment delete — clears ``deleted_at`` (== the given timestamp) on the deployments
    and the media/observations that were deleted with them."""
    if not body.deployment_ids:
        return {"restored": 0}
    dep_to_project = await _resolve_deployment_projects(body.deployment_ids, only_active=False)
    if not dep_to_project:
        return {"restored": 0}
    for project_id in set(dep_to_project.values()):
        await assert_project_writer(user.id, project_id)

    found_ids = list(dep_to_project.keys())
    await asyncio.to_thread(lambda: restore_deployments(create_service_client(), found_ids, body.deleted_at))
    return {"restored": len(found_ids)}


LOCATION_FORBIDDEN = (
    "You can change a deployment's location if you set it up and are still a member of its project, or if you are an admin of the project."
)


class DeploymentLocationRequest(BaseModel):
    """The whole location of one deployment. Every field is written, so a blank clears it."""

    location_name: str
    location_description: Optional[str] = None
    latitude: Optional[float] = Field(None, ge=-90, le=90, allow_inf_nan=False)
    longitude: Optional[float] = Field(None, ge=-180, le=180, allow_inf_nan=False)
    altitude: Optional[float] = Field(None, allow_inf_nan=False)
    accuracy: Optional[float] = Field(None, ge=0, allow_inf_nan=False)

    @field_validator("location_name")
    @classmethod
    def _name_required(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("location_name is required")
        return v

    @field_validator("location_description")
    @classmethod
    def _blank_description_is_none(cls, v: Optional[str]) -> Optional[str]:
        return (v or "").strip() or None

    @model_validator(mode="after")
    def _coordinates_pair(self) -> "DeploymentLocationRequest":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude go together: give both or neither")
        return self


@router.patch("/{deployment_id}/location", dependencies=[Depends(require_not_demo)])
async def update_deployment_location(
    deployment_id: str,
    body: DeploymentLocationRequest,
    user_client: Any = Depends(get_user_client),
) -> ApiResponse:
    """Correct a deployment's location as the signed-in user, with ``timezone`` recomputed.

    Runs on the caller's client, so RLS decides: the deployment's creator while they hold
    project_member on its project, or a project_admin. A refusal is 403, a deployment the caller
    cannot see is 404. Returns the location columns as the database now holds them.
    """
    try:
        dep_id = str(uuid.UUID(deployment_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Deployment not found")

    def _apply():
        return apply_location_update(user_client, dep_id, location_update(body.model_dump()))

    try:
        outcome, row = await asyncio.to_thread(_apply)
    except APIError as exc:
        if exc.code == "23514":
            raise HTTPException(status_code=422, detail=f"That location is not allowed: {exc.message}")
        if exc.code == "42501":
            raise HTTPException(status_code=403, detail=LOCATION_FORBIDDEN)
        raise
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="Deployment not found")
    if outcome == "forbidden":
        raise HTTPException(status_code=403, detail=LOCATION_FORBIDDEN)
    return ApiResponse(data=row)


@router.post("/validate")
async def validate_deployments(
    request: ValidateDeploymentsRequest, user: Any = Depends(get_current_user), user_client: Any = Depends(get_user_client)
) -> ApiResponse:
    """
    Given a list of deployment IDs (or 8-char folder prefixes), determine their state:
    - 'valid': exists and user has access
    - 'no_access': exists but user does not belong to project
    - 'not_found': does not exist

    Returned inside the standard ``{data, error, meta}`` envelope (``data`` is the
    ``{id: status}`` map). The frontend pre-upload banner reads ``response.data``.
    """
    if not request.deployment_ids:
        return ApiResponse(data={})

    results = {dep_id: "not_found" for dep_id in request.deployment_ids}

    admin_client = create_service_client()

    # Dedupe: a batch upload from one folder repeats the same prefix on every
    # image, and the prefix branch runs one query each — don't look them up N times.
    full_uuids = list({d for d in request.deployment_ids if len(d) > 8})
    prefixes = list({d for d in request.deployment_ids if len(d) == 8})

    user_found_ids: set[str] = set()
    admin_found_ids: set[str] = set()

    # 1. Check full UUIDs. An exception here is a real DB error (RLS filters rows,
    # it never raises), so log it instead of silently treating the id as missing.
    if full_uuids:
        try:
            admin_res = admin_client.table("deployments").select("id").in_("id", full_uuids).execute()
            admin_found_ids.update(r["id"].lower() for r in admin_res.data)
        except Exception as exc:
            logger.warning("validate_admin_uuid_lookup_failed", error=str(exc))

        try:
            user_res = user_client.table("deployments").select("id").in_("id", full_uuids).execute()
            user_found_ids.update(r["id"].lower() for r in user_res.data)
        except Exception as exc:
            logger.warning("validate_user_uuid_lookup_failed", error=str(exc))

    # 2. Check 8-hex folder prefixes via a uuid range (NOT ilike — deployments.id
    # is a uuid column; see deployment_id_prefix_bounds).
    for prefix in prefixes:
        bounds = deployment_id_prefix_bounds(prefix)
        if not bounds:
            continue  # not 8-hex → cannot match a uuid; leave as not_found
        lo, hi = bounds
        try:
            admin_res = admin_client.table("deployments").select("id").gte("id", lo).lte("id", hi).limit(1).execute()
            if admin_res.data:
                admin_found_ids.add(prefix.lower())
        except Exception as exc:
            logger.warning("validate_admin_prefix_lookup_failed", prefix=prefix, error=str(exc))

        try:
            user_res = user_client.table("deployments").select("id").gte("id", lo).lte("id", hi).limit(1).execute()
            if user_res.data:
                user_found_ids.add(prefix.lower())
        except Exception as exc:
            logger.warning("validate_user_prefix_lookup_failed", prefix=prefix, error=str(exc))

    # 3. Compile results
    for dep_id in request.deployment_ids:
        lower_id = dep_id.lower()
        if lower_id in user_found_ids:
            results[dep_id] = "valid"
        elif lower_id in admin_found_ids:
            results[dep_id] = "no_access"
        else:
            results[dep_id] = "not_found"

    return ApiResponse(data=results)


@router.post("/backfill-timezones")
async def backfill_timezones(
    user: Any = Depends(get_current_user),
) -> Dict[str, int]:
    """Resolve and persist ``deployments.timezone`` for deployments that lack it.

    Idempotent maintenance task: for every deployment with coordinates but no
    timezone, derive the IANA zone from latitude/longitude (timezonefinder) and
    store it so the UI can render capture times in local time. Run once after the
    timezone column is deployed; new CamtrapDP imports populate it automatically.
    """
    from app.domain.photo_preprocessing import resolve_timezone

    svc = create_service_client()

    def _do_backfill() -> tuple[int, int]:
        rows = (
            svc.table("deployments")
            .select("id, latitude, longitude, timezone")
            .is_("timezone", "null")
            .not_.is_("latitude", "null")
            .not_.is_("longitude", "null")
            .execute()
            .data
            or []
        )
        updated = 0
        for dep in rows:
            tz = resolve_timezone(dep.get("latitude"), dep.get("longitude"))
            if not tz:
                continue
            svc.table("deployments").update({"timezone": tz}).eq("id", dep["id"]).execute()
            updated += 1
        return len(rows), updated

    # The query + per-row update loop are synchronous Supabase calls; run them off
    # the event loop so a large backfill can't freeze the API.
    candidates, updated = await asyncio.to_thread(_do_backfill)
    return {"candidates": candidates, "updated": updated}
