# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Project endpoints — user-facing project creation (e.g. from the upload flow)."""

import asyncio
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.authz import project_restore_right
from app.dependencies import get_user_client, get_verified_user, require_not_demo
from app.domain.soft_delete import now_iso, restore_project, soft_delete_project_as_user
from app.services.db_utils import rows_of
from app.services.supabase_client import create_service_client

router = APIRouter(prefix="/api/projects", tags=["projects"])


class CreateProjectRequest(BaseModel):
    name: str
    description: Optional[str] = None


@router.post("")
async def create_project(
    body: CreateProjectRequest,
    user: Any = Depends(get_verified_user),
) -> Dict[str, Any]:
    """Create a project in the caller's organisation.

    The caller becomes ``project_admin`` automatically via the ``handle_new_project`` DB trigger
    (which reads ``created_by``), mirroring the CamtrapDP import. Requires the user to belong to
    an organisation. ``get_verified_user`` blocks the demo/unverified accounts.
    """
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Project name is required.")

    def _create() -> Dict[str, Any]:
        svc = create_service_client()
        # Resolve the user's primary organisation (user_roles uses scope_id + scope_type).
        org_res = svc.table("user_roles").select("scope_id").eq("user_id", user.id).eq("scope_type", "organisation").limit(1).execute()
        if not org_res.data:
            raise HTTPException(status_code=403, detail="You must belong to an organisation to create a project.")
        org_id = rows_of(org_res)[0]["scope_id"]

        project_id = str(uuid.uuid4())
        svc.table("projects").insert(
            {
                "id": project_id,
                "name": name,
                "description": body.description or "",
                "organisation_id": org_id,
                "created_by": user.id,
                "modified_by": user.id,
            }
        ).execute()
        return {"id": project_id, "name": name, "organisation_id": org_id}

    return await asyncio.to_thread(_create)


class RestoreProjectRequest(BaseModel):
    deleted_at: str


_DELETE_RULE = "only a project admin can delete or restore a project"


def _project_uuid(project_id: str) -> str:
    try:
        return str(uuid.UUID(project_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Project not found")


@router.delete("/{project_id}", dependencies=[Depends(require_not_demo)])
async def delete_project(
    project_id: str,
    user: Any = Depends(get_verified_user),
    user_client: Any = Depends(get_user_client),
) -> Dict[str, Any]:
    """Soft-delete a project and cascade to its deployments, media and observations.

    The database decides: ``soft_delete_project`` runs as the caller and allows a
    ``project_admin`` of the project or ``ww_admin``. Anyone else who can see the project, an
    organisation manager included, gets ``403``; a project the caller cannot see is ``404``.
    Returns the shared ``deleted_at`` so the client can offer an Undo.
    """
    pid = _project_uuid(project_id)
    ts = now_iso()
    outcome = await asyncio.to_thread(lambda: soft_delete_project_as_user(user_client, create_service_client(), pid, ts))
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    if outcome == "refused":
        raise HTTPException(status_code=403, detail=f"Not deleted: {_DELETE_RULE}.")
    return {"id": pid, "deleted_at": ts}


@router.post("/{project_id}/restore", dependencies=[Depends(require_not_demo)])
async def restore_project_endpoint(
    project_id: str,
    body: RestoreProjectRequest,
    user: Any = Depends(get_verified_user),
) -> Dict[str, Any]:
    """Undo a project delete: clears ``deleted_at`` (equal to the given timestamp) on the project
    and the deployments, media and observations deleted with it.

    Same rule as the delete, a ``project_admin`` or ``ww_admin``. It is checked here in Python
    (``project_restore_right``) because the database has no restore function (ww-backend #286)
    and hides a soft-deleted project from the caller's own session. ``403`` for anyone else with
    a role reaching the project, ``404`` otherwise.
    """
    pid = _project_uuid(project_id)
    right = await project_restore_right(user.id, pid)
    if right == "not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    if right == "refused":
        raise HTTPException(status_code=403, detail=f"Not restored: {_DELETE_RULE}.")
    await asyncio.to_thread(lambda: restore_project(create_service_client(), pid, body.deleted_at))
    return {"id": pid, "restored": True}
