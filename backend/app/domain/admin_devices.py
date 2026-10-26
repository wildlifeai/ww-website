# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Every device in the system, for a ``ww_admin`` (#343).

Reads as the caller, under RLS. Every signed-in user reads every camera (ww-backend#346) and
a ``ww_admin`` reads every deployment, project and organisation, so the admin the router lets
through sees them all.

Soft-deleted devices are left out. A device's latest deployment is its live deployment
with the most recent ``deployment_start``, in a live project; a deleted deployment, or one
whose project is deleted, never counts. Cameras belong to no organisation (ww-backend#346), so
a device's ``organisation`` is its latest deployment's project's, and none without one.
"""

from __future__ import annotations

from app.services.db_utils import read_all

_DEVICE_COLUMNS = "id, name, bluetooth_id, device_eui"
_DEPLOYMENT_COLUMNS = "id, name, device_id, deployment_start, deployment_end, projects(id, name, deleted_at, organisations(id, name))"


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


def list_devices(client) -> list[dict]:
    """Live devices with their latest live deployment and its project's organisation, ordered by name.

    Two paged reads (PostgREST returns at most 1,000 rows per response) with the project and
    organisation embedded, so the cost does not grow with a query per device.
    """
    devices = read_all(lambda: client.table("devices").select(_DEVICE_COLUMNS).is_("deleted_at", "null").order("id"))
    deployments = read_all(lambda: client.table("deployments").select(_DEPLOYMENT_COLUMNS).is_("deleted_at", "null").order("id"))
    latest = _latest_deployments(deployments)

    out = []
    for dev in devices:
        dep = latest.get(dev["id"])
        org = dep["projects"].get("organisations") if dep else None
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
