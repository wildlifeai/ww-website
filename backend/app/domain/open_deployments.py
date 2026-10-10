# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""One open deployment per camera (ww-backend #320).

The ``deployments_one_open_per_device`` constraint lets a device have at most one deployment
with no ``deployment_end`` that is not soft-deleted. It is deferred, so a write that breaks it
fails when its PostgREST request commits, with SQLSTATE ``23P01`` and the device id in the
error's details. It is the schema's only exclusion constraint, so the code alone identifies it.
"""

import re

EXCLUSION_VIOLATION = "23P01"

_DEVICE_ID = re.compile(r"\(device_id\)=\(([0-9a-fA-F-]{36})\)")


class OpenDeploymentConflict(Exception):
    """A write would give a camera a second open deployment. ``camera`` names it."""

    def __init__(self, camera: str):
        self.camera = camera
        super().__init__(f"Camera '{camera}' already has an open deployment")


def is_open_deployment_conflict(exc: BaseException) -> bool:
    return getattr(exc, "code", None) == EXCLUSION_VIOLATION


def conflict_camera(svc, exc: BaseException) -> str:
    """The name of the camera a ``23P01`` error is about, else its id, else ``unknown``."""
    match = _DEVICE_ID.search(str(getattr(exc, "details", None) or ""))
    if not match:
        return "unknown"
    device_id = match.group(1)
    try:
        rows = svc.table("devices").select("name").eq("id", device_id).limit(1).execute().data
    except Exception:
        rows = None
    return (rows[0].get("name") if rows else None) or device_id
