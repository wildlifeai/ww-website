# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Soft delete and restore (photos, deployments, projects).

All operations set/clear ``deleted_at``, nothing is hard-deleted, so every delete is reversible.
A single shared timestamp ``ts`` scopes one delete operation: restoring by that exact ``ts`` reverses
only that delete and never resurrects rows that were already deleted earlier.

Every function takes the caller's own client, so the database decides who may. Deployments and
projects go through ww-backend's functions (``soft_delete_deployment``, ``soft_delete_project``,
``restore_deployment``, ``restore_project``), which cascade to media and observations under
``ts`` in one transaction and raise ``42501`` for a refused caller and ``P0002`` for an unknown
id. Media are plain UPDATEs under RLS. Nothing here writes with the service role; it only reads
with it to name the camera when a restore clashes with another open deployment. Callers run these
inside ``asyncio.to_thread``, as they are synchronous Supabase calls.
"""

from datetime import datetime, timezone
from typing import Literal

from app.domain.open_deployments import OpenDeploymentConflict, conflict_camera, is_open_deployment_conflict
from app.services.supabase_client import create_service_client

REFUSED = "42501"
NOT_FOUND = "P0002"


def now_iso() -> str:
    """The shared ``ts``: ISO 8601 with microseconds and ``+00:00``, as Postgres stores it."""
    return datetime.now(timezone.utc).isoformat()


def check_deleted_at(value: str) -> str:
    """Accept a ``deleted_at`` sent back for an Undo only if Postgres reads it as one instant.

    A timestamp without an offset would be read in the database session's time zone, so it is
    refused. The string is returned unchanged; the database compares the instant, not the text.
    """
    if datetime.fromisoformat(value).tzinfo is None:
        raise ValueError("deleted_at needs a time zone offset")
    return value


def _code(exc: Exception):
    return getattr(exc, "code", None)


def _restore_conflict_camera(user_client, exc: Exception, ts: str, *, deployment_id: str | None = None, project_id: str | None = None) -> str:
    """Name the camera a ``23P01`` from ``restore_deployment`` or ``restore_project`` is about.

    The error names the device only when the session may read the row. Under the caller's session
    RLS hides it ("Key conflicts with existing key."), so the camera is then found with a
    service-role read: of the open deployments this restore would have reopened, the one whose
    camera has another open deployment. Read only, and only after the database has let the
    caller restore, since its permission check runs before the write that clashed.
    """
    camera = conflict_camera(user_client, exc)
    if camera != "unknown":
        return camera
    try:
        svc = create_service_client()
        reopened = svc.table("deployments").select("device_id").eq("deleted_at", ts).is_("deployment_end", "null")
        reopened = reopened.eq("id", deployment_id) if deployment_id else reopened.eq("project_id", project_id)
        devices = [r["device_id"] for r in (reopened.execute().data or []) if r.get("device_id")]
        if not devices:
            return camera
        open_now = svc.table("deployments").select("device_id").in_("device_id", devices).is_("deleted_at", "null").is_("deployment_end", "null")
        busy = open_now.limit(1).execute().data
        if not busy:
            return camera
        device_id = busy[0]["device_id"]
        rows = svc.table("devices").select("name").eq("id", device_id).limit(1).execute().data
        return (rows[0].get("name") if rows else None) or device_id
    except Exception:
        return camera


# ── Delete ───────────────────────────────────────────────────────────────────


def soft_delete_deployments_as_user(user_client, dep_ids: list[str], ts: str) -> tuple[list[str], list[str]]:
    """Soft-delete deployments and their media and observations as the requesting user.

    Each id goes through ``soft_delete_deployment(p_id, p_deleted_at=ts)`` on ``user_client``.
    The database allows the deployment's creator while they hold ``project_member`` on the
    project, a ``project_admin`` of the project, or ``ww_admin`` (ww-backend #266), and raises
    ``42501`` for anyone else. A plain UPDATE cannot do this: the SELECT policy hides a
    soft-deleted row, so RLS refuses an UPDATE that sets ``deleted_at``.

    Ids the caller cannot see (unknown, already deleted, or in a project they have no role on)
    are skipped, as RLS would.

    Returns ``(deleted_ids, refused_ids)``.
    """
    ids = list(dict.fromkeys(dep_ids))
    if not ids:
        return [], []
    visible = user_client.table("deployments").select("id").in_("id", ids).execute()
    visible_ids = {r["id"] for r in (visible.data or [])}

    deleted: list[str] = []
    refused: list[str] = []
    for dep_id in ids:
        if dep_id not in visible_ids:
            continue
        try:
            user_client.rpc("soft_delete_deployment", {"p_id": dep_id, "p_deleted_at": ts}).execute()
        except Exception as exc:
            if _code(exc) == REFUSED:
                refused.append(dep_id)
                continue
            if _code(exc) == NOT_FOUND:  # gone since the read above
                continue
            raise
        deleted.append(dep_id)
    return deleted, refused


def _split_changed(requested: list[str], rows) -> tuple[list[str], list[str]]:
    """Split ``requested`` (deduplicated, in order) into the ids in ``rows`` and the rest."""
    changed = {r["id"] for r in (rows or []) if isinstance(r, dict) and "id" in r}
    ids = list(dict.fromkeys(requested))
    return [i for i in ids if i in changed], [i for i in ids if i not in changed]


def soft_delete_media_as_user(user_client, media_ids: list[str], ts: str) -> tuple[list[str], list[str]]:
    """Soft-delete media as the requesting user, so RLS decides which rows change.

    The UPDATE policies allow a photo's uploader while they hold at least ``project_member`` on
    its project, or a ``project_admin`` of the project. Any other id changes 0 rows without an
    error, as does an id that is unknown or already deleted. PostgREST returns the changed rows,
    so the ids it did not return are the skipped ones.

    Returns ``(deleted_ids, skipped_ids)``.
    """
    if not media_ids:
        return [], []
    resp = user_client.table("media").update({"deleted_at": ts}).in_("id", media_ids).is_("deleted_at", "null").execute()
    return _split_changed(media_ids, resp.data)


def soft_delete_project_as_user(user_client, project_id: str, ts: str) -> Literal["deleted", "refused", "not_found"]:
    """Soft-delete a project and its deployments, media and observations as the requesting user.

    ``soft_delete_project(p_id, p_deleted_at=ts)`` runs on ``user_client``. The database allows a
    ``project_admin`` of the project or ``ww_admin`` and raises ``42501`` for anyone else, an
    organisation manager included. A project the caller cannot see (unknown, already deleted, or
    in a project they have no role on) is ``not_found`` without calling the function, as RLS
    would hide it.
    """
    visible = user_client.table("projects").select("id").eq("id", project_id).limit(1).execute()
    if not visible.data:
        return "not_found"
    try:
        user_client.rpc("soft_delete_project", {"p_id": project_id, "p_deleted_at": ts}).execute()
    except Exception as exc:
        if _code(exc) == REFUSED:
            return "refused"
        if _code(exc) == NOT_FOUND:  # gone since the read above
            return "not_found"
        raise
    return "deleted"


# ── Restore (undo), scoped to the exact delete timestamp ────────────────────


def restore_deployments_as_user(user_client, dep_ids: list[str], ts: str) -> tuple[list[str], list[str], list[str]]:
    """Clear ``deleted_at == ts`` on deployments and their media and observations, as the user.

    Each id goes through ``restore_deployment(p_id, p_deleted_at=ts)`` on ``user_client``, under
    the delete rule. There is no visibility read first: the SELECT policy hides a soft-deleted
    row from everyone, its creator included. An unknown id (``P0002``) is skipped, and so is one
    with nothing deleted at ``ts`` (the function returns false).

    Each call is its own transaction. An open deployment whose camera has since got another open
    deployment fails with ``23P01`` (one per camera, ww-backend #320) and rolls back only that
    deployment, its photos and observations included; the camera's name
    (``_restore_conflict_camera``) is collected and the other ids carry on.

    Returns ``(restored_ids, refused_ids, conflicting_cameras)``.
    """
    restored: list[str] = []
    refused: list[str] = []
    cameras: list[str] = []
    for dep_id in dict.fromkeys(dep_ids):
        try:
            resp = user_client.rpc("restore_deployment", {"p_id": dep_id, "p_deleted_at": ts}).execute()
        except Exception as exc:
            if _code(exc) == REFUSED:
                refused.append(dep_id)
                continue
            if _code(exc) == NOT_FOUND:
                continue
            if is_open_deployment_conflict(exc):
                cameras.append(_restore_conflict_camera(user_client, exc, ts, deployment_id=dep_id))
                continue
            raise
        if resp.data is True:
            restored.append(dep_id)
    return restored, refused, cameras


def restore_media_as_user(user_client, media_ids: list[str], ts: str) -> tuple[list[str], list[str]]:
    """Clear ``deleted_at == ts`` on media as the requesting user, under the same RLS rule as
    ``soft_delete_media_as_user``.

    Returns ``(restored_ids, skipped_ids)``.
    """
    if not media_ids:
        return [], []
    resp = user_client.table("media").update({"deleted_at": None}).in_("id", media_ids).eq("deleted_at", ts).execute()
    return _split_changed(media_ids, resp.data)


def restore_project_as_user(user_client, project_id: str, ts: str) -> Literal["restored", "unchanged", "refused", "not_found"]:
    """Clear ``deleted_at == ts`` on a project and the deployments, media and observations deleted
    with it, as the requesting user.

    ``restore_project(p_id, p_deleted_at=ts)`` runs on ``user_client`` under the delete rule
    (``42501`` is ``refused``, ``P0002`` is ``not_found``). ``unchanged`` when the project exists
    but nothing was deleted at ``ts``, for example an Undo that already ran.

    Raises ``OpenDeploymentConflict`` when an open deployment's camera has since got another open
    deployment (``23P01``, ww-backend #320). The function is one transaction, so nothing is
    restored. ``_restore_conflict_camera`` names the camera.
    """
    try:
        resp = user_client.rpc("restore_project", {"p_id": project_id, "p_deleted_at": ts}).execute()
    except Exception as exc:
        if _code(exc) == REFUSED:
            return "refused"
        if _code(exc) == NOT_FOUND:
            return "not_found"
        if is_open_deployment_conflict(exc):
            raise OpenDeploymentConflict(_restore_conflict_camera(user_client, exc, ts, project_id=project_id)) from exc
        raise
    return "restored" if resp.data is True else "unchanged"
