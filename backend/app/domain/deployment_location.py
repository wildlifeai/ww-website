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

``fill_missing_timezones`` is the other way a deployment gets its zone (#309): the app creates
deployments without one, so the website asks for the zones of the deployments it shows.
"""

from typing import Any, Literal, Optional

from app.domain.photo_preprocessing import resolve_timezone
from app.services.db_utils import rows_of

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


# One request's worth of deployments: what a project selection shows, well under PostgREST's
# 1,000-row cap on the read.
MAX_TIMEZONE_FILL = 500


def fill_missing_timezones(user_client: Any, service_client: Any, deployment_ids: list[str]) -> dict[str, str]:
    """Store the time zone of each listed deployment that has coordinates and no zone.

    The read runs as the caller, so only deployments they can see are filled. The write uses the
    service role, because RLS lets only the creator or a project_admin update a deployment and any
    member who views one should get local times. It is bounded to what the read returned, writes
    only ``timezone``, only while it is still empty, and only the zone of the row's own
    coordinates, so it can't change anything a person entered.

    Returns ``{deployment_id: zone}`` for the deployments given a zone.
    """
    if not deployment_ids:
        return {}
    rows = rows_of(
        user_client.table("deployments")
        .select("id, latitude, longitude")
        .in_("id", deployment_ids)
        .is_("timezone", "null")
        .is_("deleted_at", "null")
        .not_.is_("latitude", "null")
        .not_.is_("longitude", "null")
        .execute()
    )
    filled: dict[str, str] = {}
    for row in rows:
        zone = resolve_timezone(row.get("latitude"), row.get("longitude"))
        if not zone:
            continue
        service_client.table("deployments").update({"timezone": zone}).eq("id", row["id"]).is_("timezone", "null").execute()
        filled[row["id"]] = zone
    return filled
