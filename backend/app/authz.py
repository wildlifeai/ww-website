# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Object-level authorization for resource-scoped endpoints.

Many endpoints read/write a deployment / project / org / media via the
**service-role client** (which bypasses RLS). Authenticating the caller
(`get_current_user`) is not enough — we must also check the caller may access
*that* resource, or any logged-in (even unverified) user could read another
organisation's data by supplying its IDs.

Two levels, both from the caller's active, non-deleted, unexpired ``user_roles`` rows:

- **Read** (``_has_access``): any role at ``system`` scope, the resource's ``organisation``, or
  its ``project``, a ``project_viewer`` included.
- **Write** (``_can_write``): ww-backend's ``has_project_role(user, project, 'project_member')``,
  so ``project_member`` or ``project_admin`` on the resource's project, or ``ww_admin``. A
  ``project_viewer`` and every organisation role are refused (UI audit decision 1). A route that
  changes project data with the service role, or starts work on it, checks this level.

These are FastAPI dependencies meant for a route's ``dependencies=[...]`` list,
so they run alongside the endpoint's existing user dependency without changing
its signature. They raise **404** (not 403) when the caller cannot read the resource, so IDs in
other tenants can't be probed for existence, and **403** (``WRITE_REFUSED``) when they can read
it but not change it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import Depends, HTTPException

from app.dependencies import get_current_user
from app.services.db_utils import rows_of
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


_WRITE_ROLES = ("project_member", "project_admin")
_PROJECT_CREATOR_ROLES = ("organisation_member", "organisation_manager")

WRITE_REFUSED = "You need the Project Member role to change this project."


def _can_write(roles: list[dict], project_id: Optional[str]) -> bool:
    """``has_project_role(user, project_id, 'project_member')`` from the caller's active role rows.

    ``project_member`` or ``project_admin`` on the project itself, or ``ww_admin``. A
    ``project_viewer``, an ``organisation_manager`` and an ``organisation_member`` are refused,
    as the database refuses them. Pure, so the tests pin it."""
    now = datetime.now(timezone.utc)
    for r in roles:
        if not _role_active(r, now):
            continue
        if r.get("role") == "ww_admin" and r.get("scope_type") == "system":
            return True
        if project_id and r.get("scope_type") == "project" and r.get("scope_id") == project_id and r.get("role") in _WRITE_ROLES:
            return True
    return False


def _project_creator_org_ids(roles: list[dict]) -> list[str]:
    """Organisations the caller may create a project in: an active ``organisation_member`` or
    ``organisation_manager`` role there (ww-backend's projects INSERT policy). Pure."""
    now = datetime.now(timezone.utc)
    return [
        r["scope_id"]
        for r in roles
        if _role_active(r, now) and r.get("scope_type") == "organisation" and r.get("role") in _PROJECT_CREATOR_ROLES and r.get("scope_id")
    ]


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
        found = rows_of(resp)
        return found[0][col] if found else None

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
    return rows_of(resp)


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


async def assert_write(user_id: str, **resource) -> None:
    """Raise 404 unless the caller may read the resolved resource, 403 unless they may change it.

    Takes the same ids as ``assert_access``; the write rule is ``_can_write``."""

    def _check() -> Literal["allowed", "refused", "not_found"]:
        svc = create_service_client()
        org_id, project_id = _resolve_org_project(svc, **resource)
        if org_id is None and project_id is None:
            return "not_found"
        roles = _fetch_active_roles(svc, user_id)
        if _can_write(roles, project_id):
            return "allowed"
        return "refused" if _has_access(roles, org_id, project_id) else "not_found"

    outcome = await asyncio.to_thread(_check)
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="Not found")
    if outcome == "refused":
        raise HTTPException(status_code=403, detail=WRITE_REFUSED)


async def writable_media_ids(user_id: str, media_ids: list[str]) -> list[str]:
    """The ids in ``media_ids`` whose project the caller may change (``_can_write``), in order.

    For routes that take a list of photos in the body and write about them with the service
    role. Unknown ids are dropped too."""

    def _check() -> list[str]:
        if not media_ids:
            return []
        svc = create_service_client()
        roles = _fetch_active_roles(svc, user_id)
        project_of: dict[str, Optional[str]] = {}
        unique = list(dict.fromkeys(media_ids))
        for start in range(0, len(unique), 200):  # keep the in.(...) list inside a URL
            resp = svc.table("media").select("id, deployments(project_id)").in_("id", unique[start : start + 200]).execute()
            for row in rows_of(resp):
                dep = row.get("deployments")
                if isinstance(dep, list):  # PostgREST may nest a to-one as a 1-element list
                    dep = dep[0] if dep else None
                project_of[row["id"]] = dep.get("project_id") if isinstance(dep, dict) else None
        return [m for m in media_ids if m in project_of and _can_write(roles, project_of[m])]

    return await asyncio.to_thread(_check)


async def project_creator_org_id(user_id: str) -> Optional[str]:
    """The organisation a new project goes into: the first one where the caller holds an active
    ``organisation_member`` or ``organisation_manager`` role, or ``None``."""

    def _first() -> Optional[str]:
        orgs = _project_creator_org_ids(_fetch_active_roles(create_service_client(), user_id))
        return orgs[0] if orgs else None

    return await asyncio.to_thread(_first)


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
        for row in rows_of(resp):
            proj = row.get("projects")
            if isinstance(proj, list):  # PostgREST may nest a to-one as a 1-element list
                proj = proj[0] if proj else None
            org_id = proj.get("organisation_id") if isinstance(proj, dict) else None
            project_id = row.get("project_id")
            if (org_id or project_id) and _has_access(roles, org_id, project_id):
                out.append(row["id"])
        return out

    return await asyncio.to_thread(_check)


async def media_org_deployment_ids(user_id: str, media_id: str) -> list[str]:
    """Deployments in ``media_id``'s organisation that the caller may access.

    Scopes the Wildlife Brain's similarity search: the vector RPC runs as the service
    role, so without this list it would rank every organisation's photos.
    """

    def _list() -> list[str]:
        svc = create_service_client()
        org_id, _ = _resolve_org_project(svc, media_id=media_id)
        if not org_id:
            return []
        roles = _fetch_active_roles(svc, user_id)
        out: list[str] = []
        start = 0
        while True:  # PostgREST returns at most 1,000 rows a request
            rows = rows_of(
                svc.table("deployments")
                .select("id, project_id, projects!inner(organisation_id)")
                .eq("projects.organisation_id", org_id)
                .is_("deleted_at", "null")
                .order("id")
                .range(start, start + 999)
                .execute()
            )
            out += [r["id"] for r in rows if _has_access(roles, org_id, r.get("project_id"))]
            if len(rows) < 1000:
                return out
            start += 1000

    return await asyncio.to_thread(_list)


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
    """Classify each **full-UUID** deployment id as ``valid`` (exists and the caller may add photos
    to it), ``no_access`` (exists, but the caller may not), or ``not_found`` (not in the DB).

    The write rule (``_can_write``), as ww-backend's media INSERT policy: a ``project_viewer``
    gets ``no_access``. Used by the upload pipeline to enforce, server-side, that images can't be
    attached to a deployment the caller can't add to, and to decide which need a manual
    assignment instead.
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
        resp = svc.table("deployments").select("id, project_id").in_("id", list(id_map.keys())).execute()
        for row in rows_of(resp):
            row_id: str = row["id"]
            key = id_map.get(row_id.lower(), row_id)
            result[key] = "valid" if _can_write(roles, row.get("project_id")) else "no_access"
        return result

    return await asyncio.to_thread(_check)


def _is_org_manager(roles: list[dict], org_id: Optional[str]) -> bool:
    """An active ``organisation_manager`` role on the organisation itself (#307).

    The rule for managing an organisation's API keys. ``ww_admin``, a system-scope manager and
    an ``organisation_member`` are refused, since a key hands the organisation's data to a
    machine. Pure, so the tests pin it."""
    now = datetime.now(timezone.utc)
    for r in roles:
        if not _role_active(r, now):
            continue
        if r.get("role") == "organisation_manager" and r.get("scope_type") == "organisation" and org_id and r.get("scope_id") == org_id:
            return True
    return False


async def org_manager_right(user_id: str, org_id: str) -> Literal["allowed", "refused", "not_found"]:
    """Whether the caller may manage the organisation's API keys (``_is_org_manager``).

    ``not_found`` when the caller holds no organisation or system role reaching it, so ids in
    other tenants cannot be probed. ``refused`` when they reach it but are not its manager."""

    def _check() -> Literal["allowed", "refused", "not_found"]:
        roles = _fetch_active_roles(create_service_client(), user_id)
        if _is_org_manager(roles, org_id):
            return "allowed"
        return "refused" if _has_access(roles, org_id, None) else "not_found"

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


async def require_deployment_write(deployment_id: str, user=Depends(get_current_user)) -> None:
    await assert_write(user.id, deployment_id=deployment_id)


async def require_project_write(project_id: str, user=Depends(get_current_user)) -> None:
    await assert_write(user.id, project_id=project_id)


async def require_media_write(media_id: str, user=Depends(get_current_user)) -> None:
    await assert_write(user.id, media_id=media_id)


async def require_cluster_write(cluster_assignment_id: str, user=Depends(get_current_user)) -> None:
    await assert_write(user.id, cluster_assignment_id=cluster_assignment_id)


async def require_system_admin(user=Depends(get_current_user)) -> None:
    if not await is_system_admin(user.id):
        raise HTTPException(status_code=403, detail="Administrator access required")
