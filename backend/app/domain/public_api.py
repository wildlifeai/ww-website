# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Public Data API domain: data access for the /api/v1/* endpoints.

Every query is scoped to the organisation the API key belongs to. A deployment's organisation
is its project's (``projects.organisation_id``); deployments have no organisation column.
Deleted rows, and rows under a deleted deployment or project, are left out.

The queries run with the service role, so the filters here are the only thing that keeps
one organisation's data from another's key. Each list is one PostgREST request, so a page
is at most ``MAX_PAGE`` rows, PostgREST's row cap.

The CamtrapDP export is ``domain/camtrapdp_export.py``, the same job as the Download button.
"""

from typing import Any, Dict, List, Optional

import structlog
from postgrest import CountMethod

from app.services.db_utils import rows_of
from app.services.supabase_client import create_service_client

logger = structlog.get_logger()

# PostgREST returns at most this many rows a request. A page is one request.
MAX_PAGE = 1000

# The project embed every deployment-scoped read filters on.
_PROJECT = "projects!inner(name, organisation_id)"


def _in_org(query, org_id: str, deployments: str = ""):
    """Filter a query that embeds ``_PROJECT`` (under ``deployments``, when given) to live rows of ``org_id``."""
    prefix = f"{deployments}." if deployments else ""
    query = query.eq(f"{prefix}projects.organisation_id", org_id).is_(f"{prefix}projects.deleted_at", "null")
    if deployments:
        query = query.is_(f"{deployments}.deleted_at", "null")
    return query


def _one(embed):
    """PostgREST may return a to-one embed as a list."""
    if isinstance(embed, list):
        return embed[0] if embed else None
    return embed


# ── Deployments ─────────────────────────────────────────────────────


def _deployment_select(status: Optional[str]) -> str:
    statuses = "deployment_statuses!inner(value)" if status else "deployment_statuses(value)"
    return f"*, {_PROJECT}, devices(name, bluetooth_id, device_eui), {statuses}"


def _flatten_deployment(row: Dict[str, Any]) -> Dict[str, Any]:
    record = {k: v for k, v in row.items() if k not in ("projects", "devices", "deployment_statuses")}
    project = _one(row.get("projects")) or {}
    device = _one(row.get("devices")) or {}
    status = _one(row.get("deployment_statuses")) or {}
    record["project_name"] = project.get("name")
    record["device_name"] = device.get("name")
    record["status"] = status.get("value")
    return record


async def list_deployments(
    org_id: str,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[List[Dict[str, Any]], int]:
    """The organisation's live deployments, newest first. Returns (records, total)."""
    client = create_service_client()
    query = _in_org(client.table("deployments").select(_deployment_select(status), count=CountMethod.exact), org_id).is_("deleted_at", "null")
    if project_id:
        query = query.eq("project_id", project_id)
    if status:
        query = query.eq("deployment_statuses.value", status)

    response = query.order("created_at", desc=True).order("id").range(offset, offset + limit - 1).execute()
    records = [_flatten_deployment(row) for row in rows_of(response)]
    return records, response.count or 0


async def get_deployment(org_id: str, deployment_id: str) -> Optional[Dict[str, Any]]:
    """One of the organisation's live deployments, or None."""
    client = create_service_client()
    response = (
        _in_org(client.table("deployments").select(_deployment_select(None)), org_id).eq("id", deployment_id).is_("deleted_at", "null").execute()
    )
    return _flatten_deployment(rows_of(response)[0]) if response.data else None


# ── Devices and telemetry ───────────────────────────────────────────


async def list_devices(
    org_id: str,
    limit: int = 50,
    offset: int = 0,
) -> tuple[List[Dict[str, Any]], int]:
    """The organisation's own cameras (``devices.organisation_id``), newest first."""
    client = create_service_client()
    response = (
        client.table("devices")
        .select("*", count=CountMethod.exact)
        .eq("organisation_id", org_id)
        .is_("deleted_at", "null")
        .order("created_at", desc=True)
        .order("id")
        .range(offset, offset + limit - 1)
        .execute()
    )
    return rows_of(response), response.count or 0


async def get_telemetry(
    org_id: str,
    device_eui: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """A camera's LoRaWAN messages sent while deployed in the organisation's projects, newest first.

    Messages belong to the deployment's project, not the camera's organisation
    (ww-backend#323): a camera lent to another organisation's project sends that
    organisation's messages. A message with no deployment is no organisation's.
    """
    client = create_service_client()
    parsed = "lorawan_parsed_messages(battery_level, sd_card_used_capacity, model_output)"
    query = _in_org(
        client.table("lorawan_messages").select(f"id, deployment_id, received_at, {parsed}, deployments!inner(id, {_PROJECT})"),
        org_id,
        deployments="deployments",
    ).eq("device_eui", device_eui)
    if date_from:
        query = query.gte("received_at", date_from)
    if date_to:
        query = query.lte("received_at", date_to)

    response = query.order("received_at", desc=True).order("id").limit(limit).execute()
    points = []
    for row in rows_of(response):
        parsed = _one(row.get("lorawan_parsed_messages")) or {}
        points.append(
            {
                "timestamp": row.get("received_at"),
                "deployment_id": row.get("deployment_id"),
                "battery_level": parsed.get("battery_level"),
                "sd_card_used_capacity": parsed.get("sd_card_used_capacity"),
                "model_output": parsed.get("model_output"),
            }
        )
    return points


# ── Observations: one verdict per photo ─────────────────────────────

# The review states that mean a person (or consensus) validated the label. The frontend's
# HUMAN_REVIEWED_STATES in lib/observations.ts.
HUMAN_REVIEWED_STATES = frozenset({"human_reviewed", "expert_reviewed", "consensus_approved"})

_OBSERVATION_COLUMNS = (
    "id, observation_type, scientific_name, vernacular_name, taxon_id, count, life_stage, sex, behavior, "
    "classification_method, classification_probability, review_status, source_type, ai_origin, created_at"
)
# The verdict's fields a partner gets, from the observation the photo's card shows.
_VERDICT_FIELDS = (
    "observation_type",
    "scientific_name",
    "vernacular_name",
    "taxon_id",
    "count",
    "life_stage",
    "sex",
    "behavior",
    "classification_method",
    "classification_probability",
    "review_status",
    "source_type",
    "ai_origin",
)


def is_human_reviewed(o: Dict[str, Any]) -> bool:
    """The frontend's ``isHumanReviewed``: ``review_status``, else a human-authored legacy row."""
    status = o.get("review_status")
    if status and status in HUMAN_REVIEWED_STATES:
        return True
    return not status and (o.get("classification_method") == "human" or o.get("source_type") == "human")


def photo_verdict(obs: List[Dict[str, Any]]) -> tuple[Optional[Dict[str, Any]], bool]:
    """Which observation a photo's card shows, and whether it shows Empty (#170).

    A port of the frontend's ``photoVerdict`` in lib/observations.ts, which the grid uses; keep
    the two the same. A human verdict wins. Otherwise the consensus row decides presence: blank
    is Empty, anything else takes its name from the first named per-model row. With neither,
    the first row. ``obs`` is in the order the observations were written.
    """
    reviewed = [o for o in obs if is_human_reviewed(o)]
    human = next((o for o in reviewed if o.get("source_type") != "consensus"), None)
    if human is None and reviewed:
        human = reviewed[0]
    consensus = next((o for o in obs if o.get("source_type") == "consensus"), None)
    if human is None and consensus is not None:
        if consensus.get("observation_type") == "blank":
            return None, True
        named = next(
            (o for o in obs if o.get("source_type") != "consensus" and o.get("observation_type") != "blank" and o.get("scientific_name")),
            None,
        )
        return named, False
    top = human if human is not None else (obs[0] if obs else None)
    return top, top is not None and not top.get("scientific_name") and top.get("observation_type") == "blank"


def _verdict_record(media: Dict[str, Any]) -> Dict[str, Any]:
    obs = media.get("observations") or []
    label, is_empty = photo_verdict(obs)
    deployment = _one(media.get("deployments")) or {}
    record: Dict[str, Any] = {
        "media_id": media["id"],
        "deployment_id": media.get("deployment_id"),
        "project_id": deployment.get("project_id"),
        "timestamp": media.get("timestamp"),
        "is_empty": is_empty,
        "human_reviewed": any(is_human_reviewed(o) for o in obs),
        "observation_id": label.get("id") if label else None,
    }
    for field in _VERDICT_FIELDS:
        record[field] = label.get(field) if label else None
    return record


async def list_observations(
    org_id: str,
    project_id: Optional[str] = None,
    deployment_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[List[Dict[str, Any]], int]:
    """One verdict per photo, for the organisation's live photos that carry a live observation.

    Pages over photos in the order they were registered (``media.created_at``, then id), so a
    page boundary does not move as new photos arrive. Returns (records, total photos).
    """
    client = create_service_client()
    query = _in_org(
        client.table("media").select(
            f"id, deployment_id, timestamp, deployments!inner(project_id, {_PROJECT}), observations!inner({_OBSERVATION_COLUMNS})",
            count=CountMethod.exact,
        ),
        org_id,
        deployments="deployments",
    )
    query = query.is_("deleted_at", "null").is_("observations.deleted_at", "null")
    if project_id:
        query = query.eq("deployments.project_id", project_id)
    if deployment_id:
        query = query.eq("deployment_id", deployment_id)

    response = (
        query.order("created_at", foreign_table="observations")
        .order("id", foreign_table="observations")
        .order("created_at")
        .order("id")
        .range(offset, offset + limit - 1)
        .execute()
    )
    return [_verdict_record(m) for m in rows_of(response)], response.count or 0


# ── Export ──────────────────────────────────────────────────────────


async def project_in_organisation(org_id: str, project_id: str) -> bool:
    """True when ``project_id`` is a live project of ``org_id``."""
    client = create_service_client()
    response = client.table("projects").select("id").eq("id", project_id).eq("organisation_id", org_id).is_("deleted_at", "null").execute()
    return bool(response.data)
