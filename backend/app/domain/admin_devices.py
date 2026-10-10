# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Every device in the system, for a ``ww_admin`` (#343).

The database keeps devices organisation-scoped: ``can_read_device`` has no ``ww_admin``
branch (ww-backend#313), so this reads with the service role and the router gates it.

Soft-deleted devices are left out. A device's latest deployment is its live deployment
with the most recent ``deployment_start``, in a live project; a deleted deployment, or one
whose project is deleted, never counts.
"""

from __future__ import annotations

from typing import Any, Callable

# PostgREST returns at most 1,000 rows per response, so both reads page.
_PAGE = 1000

_DEVICE_COLUMNS = "id, name, bluetooth_id, device_eui, organisations(id, name)"
_DEPLOYMENT_COLUMNS = "id, name, device_id, deployment_start, deployment_end, projects(id, name, deleted_at)"


def _read_all(build_query: Callable[[], Any]) -> list[dict]:
    """Every row of a read ordered on ``id``, so the pages neither skip nor repeat rows."""
    rows: list[dict] = []
    while True:
        page = build_query().range(len(rows), len(rows) + _PAGE - 1).execute().data or []
        rows.extend(page)
        if len(page) < _PAGE:
            return rows


def _latest_deployments(deployments: list[dict]) -> dict[str, dict]:
    """The latest live deployment per device id, ties broken on id."""
    latest: dict[str, dict] = {}
    for dep in deployments:
        project = dep.get("projects")
        if not project or project.get("deleted_at"):
            continue
        current = latest.get(dep["device_id"])
        if current is None or (dep["deployment_start"], dep["id"]) > (current["deployment_start"], current["id"]):
            latest[dep["device_id"]] = dep
    return latest


def list_devices(svc) -> list[dict]:
    """Live devices with their organisation and latest live deployment, ordered by name.

    Two paged reads with the organisation and project embedded, so the cost does not grow
    with a query per device.
    """
    devices = _read_all(lambda: svc.table("devices").select(_DEVICE_COLUMNS).is_("deleted_at", "null").order("id"))
    deployments = _read_all(lambda: svc.table("deployments").select(_DEPLOYMENT_COLUMNS).is_("deleted_at", "null").order("id"))
    latest = _latest_deployments(deployments)

    out = []
    for dev in devices:
        org = dev.get("organisations")
        dep = latest.get(dev["id"])
        out.append(
            {
                "id": dev["id"],
                "name": dev["name"],
                "bluetooth_id": dev["bluetooth_id"],
                "device_eui": dev.get("device_eui"),
                "organisation": {"id": org["id"], "name": org["name"]} if org else None,
                "latest_deployment": (
                    {
                        "id": dep["id"],
                        "name": dep["name"],
                        "deployment_start": dep["deployment_start"],
                        "deployment_end": dep.get("deployment_end"),
                        "project": {"id": dep["projects"]["id"], "name": dep["projects"]["name"]},
                    }
                    if dep
                    else None
                ),
            }
        )
    out.sort(key=lambda d: ((d["name"] or "").lower(), d["id"]))
    return out
