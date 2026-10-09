# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Soft-delete cascade + restore helpers (photos → deployments → projects).

All operations set/clear ``deleted_at`` — nothing is hard-deleted, so every delete is reversible.
A single shared timestamp ``ts`` scopes one delete operation: restoring by that exact ``ts`` reverses
only that delete and never resurrects rows that were already deleted earlier. Cascade order mirrors
the ops cleanup script (observations → media → deployments → project).

Callers pass a service-role client (the endpoint runs the access guard first) and run these inside
``asyncio.to_thread``, as they're synchronous Supabase calls. The exceptions are the ``_as_user``
functions, which take the caller's own client so the database decides who may delete.
"""

from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Delete ───────────────────────────────────────────────────────────────────


def soft_delete_deployments(svc, dep_ids: list[str], ts: str) -> None:
    """Soft-delete deployments and everything under them (media + observations)."""
    if not dep_ids:
        return
    svc.table("observations").update({"deleted_at": ts}).in_("deployment_id", dep_ids).is_("deleted_at", "null").execute()
    svc.table("media").update({"deleted_at": ts}).in_("deployment_id", dep_ids).is_("deleted_at", "null").execute()
    svc.table("deployments").update({"deleted_at": ts}).in_("id", dep_ids).is_("deleted_at", "null").execute()


def _cascade_deployment_delete(svc, dep_ids: list[str], ts: str) -> None:
    """Stamp ``ts`` on deployments the database has just soft-deleted, and on their children.

    ``soft_delete_deployment`` stamps the database's own ``now()``, one per call. Overwriting it
    with the shared ``ts`` keeps one delete undoable by one timestamp, as ``restore_deployments``
    expects. Only rows already soft-deleted are touched, so this never deletes anything the
    database did not.
    """
    svc.table("deployments").update({"deleted_at": ts}).in_("id", dep_ids).not_.is_("deleted_at", "null").execute()
    svc.table("observations").update({"deleted_at": ts}).in_("deployment_id", dep_ids).is_("deleted_at", "null").execute()
    svc.table("media").update({"deleted_at": ts}).in_("deployment_id", dep_ids).is_("deleted_at", "null").execute()


def soft_delete_deployments_as_user(user_client, svc, dep_ids: list[str], ts: str) -> tuple[list[str], list[str]]:
    """Soft-delete deployments as the requesting user, so the database decides who may.

    Each id goes through ``soft_delete_deployment`` on ``user_client`` (the caller's session).
    The database allows the deployment's creator while they hold ``project_member`` on the
    project, a ``project_admin`` of the project, or ``ww_admin`` (ww-backend #266), and raises
    ``42501`` for anyone else. A plain UPDATE cannot do this: the SELECT policy hides a
    soft-deleted row, so RLS refuses an UPDATE that sets ``deleted_at``.

    Ids the caller cannot see (unknown, already deleted, or in a project they have no role on)
    are skipped, as RLS would. The function does not cascade, so the media and observations of
    each deleted deployment are soft-deleted here with ``svc``, under the same ``ts``.

    Returns ``(deleted_ids, refused_ids)``.
    """
    ids = list(dict.fromkeys(dep_ids))
    if not ids:
        return [], []
    visible = user_client.table("deployments").select("id").in_("id", ids).execute()
    visible_ids = {r["id"] for r in (visible.data or [])}

    deleted: list[str] = []
    refused: list[str] = []
    try:
        for dep_id in ids:
            if dep_id not in visible_ids:
                continue
            try:
                user_client.rpc("soft_delete_deployment", {"p_id": dep_id}).execute()
            except Exception as exc:
                code = getattr(exc, "code", None)
                if code == "42501":
                    refused.append(dep_id)
                    continue
                if code == "P0002":  # gone since the read above
                    continue
                raise
            deleted.append(dep_id)
    finally:
        # Cascade whatever the database deleted, even when a later call failed, so no
        # deployment is left deleted with live children.
        if deleted:
            _cascade_deployment_delete(svc, deleted, ts)
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


def soft_delete_project(svc, project_id: str, ts: str) -> None:
    """Soft-delete a project and its whole tree (deployments → media → observations)."""
    resp = svc.table("deployments").select("id").eq("project_id", project_id).is_("deleted_at", "null").execute()
    dep_ids = [r["id"] for r in (resp.data or [])]
    soft_delete_deployments(svc, dep_ids, ts)
    svc.table("projects").update({"deleted_at": ts}).eq("id", project_id).is_("deleted_at", "null").execute()


# ── Restore (undo) — scoped to the exact delete timestamp ────────────────────


def restore_deployments(svc, dep_ids: list[str], ts: str) -> list[str]:
    """Clear ``deleted_at == ts`` on the deployments and their media and observations.

    Returns the ids of the deployments restored.
    """
    # Restore parent-first (deployments → media → observations), the mirror of the children-first
    # delete order, so a read never briefly sees an active child under a still-deleted parent.
    if not dep_ids:
        return []
    resp = svc.table("deployments").update({"deleted_at": None}).in_("id", dep_ids).eq("deleted_at", ts).execute()
    svc.table("media").update({"deleted_at": None}).in_("deployment_id", dep_ids).eq("deleted_at", ts).execute()
    svc.table("observations").update({"deleted_at": None}).in_("deployment_id", dep_ids).eq("deleted_at", ts).execute()
    return [r["id"] for r in (resp.data or []) if isinstance(r, dict) and "id" in r]


def restore_media_as_user(user_client, media_ids: list[str], ts: str) -> tuple[list[str], list[str]]:
    """Clear ``deleted_at == ts`` on media as the requesting user, under the same RLS rule as
    ``soft_delete_media_as_user``.

    Returns ``(restored_ids, skipped_ids)``.
    """
    if not media_ids:
        return [], []
    resp = user_client.table("media").update({"deleted_at": None}).in_("id", media_ids).eq("deleted_at", ts).execute()
    return _split_changed(media_ids, resp.data)


def restore_project(svc, project_id: str, ts: str) -> None:
    # Parent-first: un-delete the project before its deployments/media/observations.
    # Read the deployment ids first (their deleted_at is unaffected by the project update).
    resp = svc.table("deployments").select("id").eq("project_id", project_id).eq("deleted_at", ts).execute()
    dep_ids = [r["id"] for r in (resp.data or [])]
    svc.table("projects").update({"deleted_at": None}).eq("id", project_id).eq("deleted_at", ts).execute()
    restore_deployments(svc, dep_ids, ts)
