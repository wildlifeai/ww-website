# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Object-level authorization for resource-scoped endpoints.

Many endpoints read/write a deployment / project / org / media via the
**service-role client** (which bypasses RLS). Authenticating the caller
(`get_current_user`) is not enough — we must also check the caller may access
*that* resource, or any logged-in (even unverified) user could read another
organisation's data by supplying its IDs.

Access model (mirrors ``user_roles`` + the ww-backend RLS): a user may access a
resource when they hold an active, non-deleted role at:
  - ``system`` scope (ww_admin / system manager) → all resources, or
  - the resource's ``organisation``, or
  - the resource's ``project``.

These are FastAPI dependencies meant for a route's ``dependencies=[...]`` list,
so they run alongside the endpoint's existing user dependency without changing
its signature. They raise **404** (not 403) on denial so resource IDs in other
tenants can't be probed for existence.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import Depends, HTTPException

from app.dependencies import get_current_user
from app.services.supabase_client import create_service_client


def _role_active(row: dict, now: datetime) -> bool:
    exp = row.get("expires_at")
    if not exp:
        return True
    try:
        return datetime.fromisoformat(str(exp).replace("Z", "+00:00")) > now
    except (ValueError, TypeError):
        return True  # unparseable expiry → don't lock the user out on a bad value


def _has_access(roles: list[dict], org_id: Optional[str], project_id: Optional[str]) -> bool:
    """Decide access from the caller's active role rows (pure — unit-tested)."""
    now = datetime.now(timezone.utc)
    for r in roles:
        if not _role_active(r, now):
            continue
        scope = r.get("scope_type")
        sid = r.get("scope_id")
        if scope == "system":
            return True  # ww_admin / system-scope manager → global
        if scope == "organisation" and org_id and sid == org_id:
            return True
        if scope == "project" and project_id and sid == project_id:
            return True
    return False


def _resolve_org_project(
    svc,
    *,
    deployment_id: Optional[str] = None,
    project_id: Optional[str] = None,
    media_id: Optional[str] = None,
    cluster_assignment_id: Optional[str] = None,
    org_id: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Resolve a resource to its ``(organisation_id, project_id)`` via the chain
    media → deployment → project → organisation. Returns ``(None, None)`` when the
    resource doesn't exist (treated as no-access → 404)."""

    def _one(table: str, col: str, key: str) -> Optional[str]:
        resp = svc.table(table).select(col).eq("id", key).limit(1).execute()
        rows = resp.data or []
        return rows[0][col] if rows else None

    if cluster_assignment_id and not deployment_id:
        deployment_id = _one("cluster_assignments", "deployment_id", cluster_assignment_id)
        if not deployment_id:
            return (None, None)
    if media_id and not deployment_id:
        deployment_id = _one("media", "deployment_id", media_id)
        if not deployment_id:
            return (None, None)
    if deployment_id and not project_id:
        project_id = _one("deployments", "project_id", deployment_id)
        if not project_id:
            return (None, None)
    if project_id and not org_id:
        org_id = _one("projects", "organisation_id", project_id)
        if not org_id:
            return (None, None)
    return (org_id, project_id)


def _fetch_active_roles(svc, user_id: str) -> list[dict]:
    resp = (
        svc.table("user_roles")
        .select("role, scope_type, scope_id, expires_at")
        .eq("user_id", user_id)
        .eq("is_active", True)
        .is_("deleted_at", "null")
        .execute()
    )
    return resp.data or []


async def assert_access(user_id: str, **resource) -> None:
    """Raise 404 unless ``user_id`` may access the resolved resource."""

    def _check() -> bool:
        svc = create_service_client()
        org_id, project_id = _resolve_org_project(svc, **resource)
        # org-scoped resources pass org_id straight through, so (None, None) here
        # means the resource genuinely doesn't exist.
        if org_id is None and project_id is None:
            return False
        return _has_access(_fetch_active_roles(svc, user_id), org_id, project_id)

    if not await asyncio.to_thread(_check):
        raise HTTPException(status_code=404, detail="Not found")


async def accessible_deployment_ids(user_id: str, deployment_ids: list[str]) -> list[str]:
    """Filter a list of deployment IDs to those the caller may access (for body lists)."""

    def _check() -> list[str]:
        if not deployment_ids:
            return []
        svc = create_service_client()
        roles = _fetch_active_roles(svc, user_id)
        # One query (deployment → project → org via embed) instead of 2 per id —
        # avoids an N+1 when the body lists many deployments.
        resp = svc.table("deployments").select("id, project_id, projects(organisation_id)").in_("id", deployment_ids).execute()
        out: list[str] = []
        for row in resp.data or []:
            proj = row.get("projects")
            if isinstance(proj, list):  # PostgREST may nest a to-one as a 1-element list
                proj = proj[0] if proj else None
            org_id = proj.get("organisation_id") if isinstance(proj, dict) else None
            project_id = row.get("project_id")
            if (org_id or project_id) and _has_access(roles, org_id, project_id):
                out.append(row["id"])
        return out

    return await asyncio.to_thread(_check)


def deployment_id_prefix_bounds(prefix: str) -> tuple[str, str] | None:
    """UUID range bounds for an 8-hex camera folder prefix (e.g. ``MEDIA/7785FABB/``).

    ``deployments.id`` is a ``uuid`` column, so filtering it with
    ``ILIKE '{prefix}%'`` raises Postgres 42883 (``operator does not exist: uuid
    ~~* unknown``). That error was swallowed at every call site, which silently
    dropped BMP frames (they bind by folder prefix, not EXIF) and made
    ``/deployments/validate`` misreport existing deployments as ``not_found``.

    A UUID's canonical text begins with its ``time_low`` field — the first 8 hex
    digits, which is exactly our folder prefix — and uuid ordering matches that
    hex order, so every id whose text starts with ``prefix`` falls in
    ``[{prefix}-0000-…-…0000, {prefix}-ffff-…-…ffff]``. Range-scanning that band
    on the uuid index needs no cast and no ``ilike``.

    Returns ``(lo, hi)`` for a valid 8-char hex prefix, else ``None``.
    """
    p = prefix.lower()
    if len(p) != 8 or any(c not in "0123456789abcdef" for c in p):
        return None
    return f"{p}-0000-0000-0000-000000000000", f"{p}-ffff-ffff-ffff-ffffffffffff"


async def classify_deployment_access(user_id: str, deployment_ids: list[str]) -> dict[str, str]:
    """Classify each **full-UUID** deployment id as ``valid`` (exists + caller has access),
    ``no_access`` (exists but caller lacks a role), or ``not_found`` (not in the DB).

    Role-based, consistent with ``accessible_deployment_ids`` / the ww-backend RLS. Used by the
    upload pipeline to enforce — server-side — that images can't be attached to a deployment the
    caller can't access, and to decide which need a manual assignment instead.
    """

    def _check() -> dict[str, str]:
        result = {d: "not_found" for d in deployment_ids}
        if not deployment_ids:
            return result
        # Postgres compares UUIDs case-insensitively but returns them lowercase, so a mixed-case
        # input id wouldn't match row["id"] and would be wrongly left "not_found". Key by lowercase
        # and map results back to the caller's original casing.
        id_map = {d.lower(): d for d in deployment_ids}
        svc = create_service_client()
        roles = _fetch_active_roles(svc, user_id)
        resp = svc.table("deployments").select("id, project_id, projects(organisation_id)").in_("id", list(id_map.keys())).execute()
        for row in resp.data or []:
            proj = row.get("projects")
            if isinstance(proj, list):  # PostgREST may nest a to-one as a 1-element list
                proj = proj[0] if proj else None
            org_id = proj.get("organisation_id") if isinstance(proj, dict) else None
            project_id = row.get("project_id")
            key = id_map.get(row["id"].lower(), row["id"])
            result[key] = "valid" if _has_access(roles, org_id, project_id) else "no_access"
        return result

    return await asyncio.to_thread(_check)


def _may_delete_deployment(roles: list[dict], user_id: str, setup_by: Optional[str], project_id: Optional[str]) -> bool:
    """The database's rule for deleting a deployment (``soft_delete_deployment``, ww-backend #266),
    restated for the one path with no database function to call: undoing a delete.

    Its creator while they hold ``project_member`` (or ``project_admin``) on the project, any
    ``project_admin`` of the project, or ``ww_admin``. An organisation manager, a project viewer
    and any other system-scope role are refused, as ``has_project_role`` refuses them. Pure, so
    the tests pin it to the database's wording."""
    now = datetime.now(timezone.utc)
    for r in roles:
        if not _role_active(r, now):
            continue
        scope = r.get("scope_type")
        role = r.get("role")
        if scope == "system" and role == "ww_admin":
            return True
        if scope == "project" and project_id and r.get("scope_id") == project_id:
            if role == "project_admin":
                return True
            if role == "project_member" and setup_by and setup_by == user_id:
                return True
    return False


async def split_deployments_by_delete_right(user_id: str, deployment_ids: list[str]) -> tuple[list[str], list[str]]:
    """Split deployment ids, deleted or not, into ``(allowed, refused)`` by the database's delete
    rule (``_may_delete_deployment``). Ids that do not exist are in neither list.

    Restoring a deployment runs with the service role, because the SELECT policy hides a
    soft-deleted row from its own creator and ww-backend has no restore function yet. This check
    is what keeps Undo to the people the database lets delete."""

    def _check() -> tuple[list[str], list[str]]:
        ids = list(dict.fromkeys(deployment_ids))
        if not ids:
            return [], []
        svc = create_service_client()
        roles = _fetch_active_roles(svc, user_id)
        resp = svc.table("deployments").select("id, project_id, setup_by").in_("id", ids).execute()
        allowed: list[str] = []
        refused: list[str] = []
        for row in resp.data or []:
            if _may_delete_deployment(roles, user_id, row.get("setup_by"), row.get("project_id")):
                allowed.append(row["id"])
            else:
                refused.append(row["id"])
        return allowed, refused

    return await asyncio.to_thread(_check)


def _may_delete_project(roles: list[dict], project_id: Optional[str]) -> bool:
    """The database's rule for deleting a project (``soft_delete_project``), restated for the one
    path with no database function to call: undoing a delete (ww-backend #286).

    A ``project_admin`` of the project, or ``ww_admin``. An organisation manager, a project member
    or viewer and any other system-scope role are refused, as ``has_project_role`` refuses them.
    Pure, so the tests pin it to the database's wording."""
    now = datetime.now(timezone.utc)
    for r in roles:
        if not _role_active(r, now):
            continue
        scope = r.get("scope_type")
        role = r.get("role")
        if scope == "system" and role == "ww_admin":
            return True
        if scope == "project" and project_id and r.get("scope_id") == project_id and role == "project_admin":
            return True
    return False


async def project_restore_right(user_id: str, project_id: str) -> Literal["allowed", "refused", "not_found"]:
    """Whether the caller may undo a project delete, by the database's delete rule
    (``_may_delete_project``).

    ``not_found`` when the project does not exist or the caller holds no role reaching it, so ids
    in other tenants cannot be probed. ``refused`` when they reach it but may not delete it.
    Restoring runs with the service role, because the SELECT policy hides a soft-deleted project
    and ww-backend has no restore function yet."""

    def _check() -> Literal["allowed", "refused", "not_found"]:
        svc = create_service_client()
        org_id, pid = _resolve_org_project(svc, project_id=project_id)
        if pid is None:
            return "not_found"
        roles = _fetch_active_roles(svc, user_id)
        if _may_delete_project(roles, pid):
            return "allowed"
        return "refused" if _has_access(roles, org_id, pid) else "not_found"

    return await asyncio.to_thread(_check)


async def is_system_admin(user_id: str) -> bool:
    def _check() -> bool:
        svc = create_service_client()
        return any(r.get("scope_type") == "system" for r in _fetch_active_roles(svc, user_id))

    return await asyncio.to_thread(_check)


# ── FastAPI dependencies (use in a route's dependencies=[...]) ────────────────


async def require_deployment_access(deployment_id: str, user=Depends(get_current_user)) -> None:
    await assert_access(user.id, deployment_id=deployment_id)


async def require_project_access(project_id: str, user=Depends(get_current_user)) -> None:
    await assert_access(user.id, project_id=project_id)


async def require_org_access(org_id: str, user=Depends(get_current_user)) -> None:
    await assert_access(user.id, org_id=org_id)


async def require_media_access(media_id: str, user=Depends(get_current_user)) -> None:
    await assert_access(user.id, media_id=media_id)


async def require_cluster_access(cluster_assignment_id: str, user=Depends(get_current_user)) -> None:
    await assert_access(user.id, cluster_assignment_id=cluster_assignment_id)


async def require_system_admin(user=Depends(get_current_user)) -> None:
    if not await is_system_admin(user.id):
        raise HTTPException(status_code=403, detail="Administrator access required")
