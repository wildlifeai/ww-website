# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Edit a deployment's location as the signed-in user (ww-website#288).

Location lives on each ``deployments`` row; there is no locations table. The write runs on the
caller's own Supabase client, never the service role, so RLS decides who may edit: the creator
while they still hold project_member on the project, or a project_admin (ww-backend#260). RLS
refuses an UPDATE silently, as 0 rows, so the update asks for the row back and an empty answer is
a refusal.

``timezone`` is recomputed from the new coordinates, the only derived value a location edit
re-runs (ww-backend#260). The database rebuilds ``location`` from latitude and longitude itself
(``sync_geolocation``).

Callers run these inside ``asyncio.to_thread``: they are synchronous Supabase calls, and the first
``resolve_timezone`` loads the timezonefinder dataset.
"""

from typing import Any, Literal, Optional

from app.domain.photo_preprocessing import resolve_timezone

# What the edit form shows and the endpoint returns.
LOCATION_COLUMNS = (
    "id",
    "location_name",
    "location_description",
    "latitude",
    "longitude",
    "altitude",
    "accuracy",
    "timezone",
)

Outcome = Literal["updated", "forbidden", "not_found"]


def location_update(fields: dict[str, Any]) -> dict[str, Any]:
    """The row patch: the six location columns as given, plus ``timezone`` from the coordinates.

    No coordinates means no time zone, so display falls back as it does for any deployment
    without GPS.
    """
    return {**fields, "timezone": resolve_timezone(fields.get("latitude"), fields.get("longitude"))}


def apply_location_update(user_client: Any, deployment_id: str, update: dict[str, Any]) -> tuple[Outcome, Optional[dict]]:
    """Write ``update`` as the caller and return what the database now holds.

    0 rows updated is ``forbidden`` when the caller can still read the deployment (RLS refused
    the write) and ``not_found`` when they cannot see it at all.
    """
    rows = user_client.table("deployments").update(update).eq("id", deployment_id).is_("deleted_at", "null").execute().data
    if rows:
        return "updated", {k: rows[0].get(k) for k in LOCATION_COLUMNS}
    visible = user_client.table("deployments").select("id").eq("id", deployment_id).is_("deleted_at", "null").execute().data
    return ("forbidden" if visible else "not_found"), None
