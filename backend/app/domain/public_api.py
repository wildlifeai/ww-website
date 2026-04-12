# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Public Data API domain — scoped data access and CamtrapDP export.

Handles all read queries for the /api/v1/* endpoints. Queries are
scoped to the organisation attached to the API key.

CamtrapDP: Camera Trap Data Package (TDWG standard) for interoperability
with Wildlife Insights, TRAPPER, EcoSecrets, and GBIF.
See: https://camtrap-dp.tdwg.org/
"""

import csv
import io
import json
import zipfile
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

import structlog

from app.services.supabase_client import create_service_client

logger = structlog.get_logger()


class PublicApiError(Exception):
    """Raised when a public API query fails."""

    pass


# ── Scoped data queries ─────────────────────────────────────────────

async def list_deployments(
    org_id: str,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[List[Dict[str, Any]], int]:
    """List deployments for an organisation.

    Returns:
        Tuple of (records, total_count).
    """
    client = create_service_client()

    query = (
        client.table("deployments")
        .select(
            "*, projects(name), devices(name, bluetooth_id)",
            count="exact",
        )
        .eq("organisation_id", org_id)
        .is_("deleted_at", "null")
    )

    if project_id:
        query = query.eq("project_id", project_id)
    if status:
        query = query.eq("status", status)

    response = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()

    # Flatten joined data
    records = []
    for row in response.data or []:
        record = {**row}
        if "projects" in record and record["projects"]:
            record["project_name"] = record["projects"].get("name")
        if "devices" in record and record["devices"]:
            record["device_name"] = record["devices"].get("name")
        record.pop("projects", None)
        record.pop("devices", None)
        records.append(record)

    total = response.count or len(records)
    return records, total


async def get_deployment(org_id: str, deployment_id: str) -> Optional[Dict[str, Any]]:
    """Get a single deployment by ID (scoped to organisation)."""
    client = create_service_client()

    response = (
        client.table("deployments")
        .select("*, projects(name), devices(name, bluetooth_id, lorawan_device_eui)")
        .eq("id", deployment_id)
        .eq("organisation_id", org_id)
        .is_("deleted_at", "null")
        .execute()
    )

    if not response.data:
        return None

    row = response.data[0]
    if "projects" in row and row["projects"]:
        row["project_name"] = row["projects"].get("name")
    if "devices" in row and row["devices"]:
        row["device_name"] = row["devices"].get("name")
    row.pop("projects", None)
    row.pop("devices", None)

    return row


async def list_devices(
    org_id: str,
    limit: int = 50,
    offset: int = 0,
) -> tuple[List[Dict[str, Any]], int]:
    """List devices for an organisation."""
    client = create_service_client()

    response = (
        client.table("devices")
        .select("*", count="exact")
        .eq("organisation_id", org_id)
        .is_("deleted_at", "null")
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )

    return response.data or [], response.count or 0


async def get_telemetry(
    org_id: str,
    device_eui: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Get telemetry time-series for a device (scoped to org)."""
    client = create_service_client()

    # Verify device belongs to org
    device_check = (
        client.table("devices")
        .select("id")
        .eq("lorawan_device_eui", device_eui)
        .eq("organisation_id", org_id)
        .execute()
    )

    if not device_check.data:
        raise PublicApiError(f"Device {device_eui} not found in organisation")

    query = (
        client.table("lorawan_parsed_messages")
        .select("battery_level, sd_card_used_capacity, model_output, received_at")
        .eq("device_eui", device_eui)
    )

    if date_from:
        query = query.gte("received_at", date_from)
    if date_to:
        query = query.lte("received_at", date_to)

    response = query.order("received_at", desc=True).limit(limit).execute()

    return [
        {
            "timestamp": row.get("received_at"),
            "battery_level": row.get("battery_level"),
            "sd_card_used_capacity": row.get("sd_card_used_capacity"),
            "model_output": row.get("model_output"),
        }
        for row in (response.data or [])
    ]


async def list_observations(
    org_id: str,
    deployment_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[List[Dict[str, Any]], int]:
    """List AI detection observations from LoRaWAN model output."""
    client = create_service_client()

    query = (
        client.table("lorawan_parsed_messages")
        .select(
            "id, device_eui, deployment_id, model_output, received_at",
            count="exact",
        )
        .not_.is_("model_output", "null")
    )

    if deployment_id:
        query = query.eq("deployment_id", deployment_id)

    response = query.order("received_at", desc=True).range(offset, offset + limit - 1).execute()

    observations = []
    for row in (response.data or []):
        model_out = row.get("model_output", {})
        if isinstance(model_out, dict):
            observations.append({
                "id": row["id"],
                "device_eui": row.get("device_eui"),
                "deployment_id": row.get("deployment_id"),
                "detection_class": model_out.get("detection") or model_out.get("class"),
                "confidence": model_out.get("confidence"),
                "timestamp": row.get("received_at"),
                "raw_output": model_out,
            })

    return observations, response.count or 0


# ── CamtrapDP export ────────────────────────────────────────────────

CAMTRAPDP_VERSION = "1.0"


async def generate_camtrapdp_package(
    org_id: str,
    project_id: Optional[str] = None,
    deployment_ids: Optional[List[str]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    include_observations: bool = True,
) -> bytes:
    """Generate a CamtrapDP-compliant data package.

    Structure:
        datapackage.json   — Frictionless Data Package descriptor
        deployments.csv    — One row per deployment
        media.csv          — One row per LoRaWAN message (event)
        observations.csv   — One row per AI detection

    Returns:
        Bytes of the ZIP file.
    """
    client = create_service_client()

    # 1. Fetch deployments
    dep_query = (
        client.table("deployments")
        .select("*, projects(name), devices(name, bluetooth_id)")
        .eq("organisation_id", org_id)
        .is_("deleted_at", "null")
    )
    if project_id:
        dep_query = dep_query.eq("project_id", project_id)
    if deployment_ids:
        dep_query = dep_query.in_("id", deployment_ids)

    dep_response = dep_query.order("deployment_start", desc=True).execute()
    deployments = dep_response.data or []

    if not deployments:
        raise PublicApiError("No deployments found matching the criteria")

    dep_id_list = [d["id"] for d in deployments]

    # 2. Fetch LoRaWAN messages for these deployments
    msg_query = (
        client.table("lorawan_parsed_messages")
        .select("*")
        .in_("deployment_id", dep_id_list)
    )
    if date_from:
        msg_query = msg_query.gte("received_at", date_from)
    if date_to:
        msg_query = msg_query.lte("received_at", date_to)

    msg_response = msg_query.order("received_at").execute()
    messages = msg_response.data or []

    # 3. Build CSVs
    dep_csv = _build_deployments_csv(deployments)
    media_csv = _build_media_csv(messages)
    obs_csv = _build_observations_csv(messages) if include_observations else ""

    # 4. Build datapackage.json descriptor
    descriptor = _build_datapackage_descriptor(
        org_id=org_id,
        num_deployments=len(deployments),
        num_media=len(messages),
        date_from=date_from,
        date_to=date_to,
    )

    # 5. Package as ZIP
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("datapackage.json", json.dumps(descriptor, indent=2))
        zf.writestr("deployments.csv", dep_csv)
        zf.writestr("media.csv", media_csv)
        if obs_csv:
            zf.writestr("observations.csv", obs_csv)

    result = buf.getvalue()

    logger.info(
        "camtrapdp_generated",
        org_id=org_id,
        deployments=len(deployments),
        messages=len(messages),
        size_bytes=len(result),
    )

    return result


def _build_deployments_csv(deployments: List[Dict[str, Any]]) -> str:
    """Build CamtrapDP deployments.csv."""
    output = io.StringIO()
    writer = csv.writer(output)

    # CamtrapDP required fields
    headers = [
        "deploymentID",
        "locationName",
        "latitude",
        "longitude",
        "deploymentStart",
        "deploymentEnd",
        "cameraModel",
        "cameraHeight",
        "captureMethod",
        "featureType",
        "habitat",
    ]
    writer.writerow(headers)

    for dep in deployments:
        project = dep.get("projects", {}) or {}
        writer.writerow([
            dep.get("id", ""),
            dep.get("location_name") or project.get("name", ""),
            dep.get("latitude", ""),
            dep.get("longitude", ""),
            dep.get("deployment_start", ""),
            dep.get("deployment_end", ""),
            dep.get("camera_model", "Wildlife Watcher"),
            dep.get("camera_height", ""),
            dep.get("capture_method", "motionDetection"),
            dep.get("feature_type", ""),
            dep.get("habitat", ""),
        ])

    return output.getvalue()


def _build_media_csv(messages: List[Dict[str, Any]]) -> str:
    """Build CamtrapDP media.csv (events from LoRaWAN messages)."""
    output = io.StringIO()
    writer = csv.writer(output)

    headers = [
        "mediaID",
        "deploymentID",
        "captureMethod",
        "timestamp",
        "filePath",
        "fileMediatype",
    ]
    writer.writerow(headers)

    for msg in messages:
        writer.writerow([
            msg.get("id", ""),
            msg.get("deployment_id", ""),
            "sensor",
            msg.get("received_at", ""),
            "",  # No file path for LoRaWAN events
            "application/json",
        ])

    return output.getvalue()


def _build_observations_csv(messages: List[Dict[str, Any]]) -> str:
    """Build CamtrapDP observations.csv from model output."""
    output = io.StringIO()
    writer = csv.writer(output)

    headers = [
        "observationID",
        "deploymentID",
        "mediaID",
        "eventID",
        "eventStart",
        "eventEnd",
        "observationType",
        "scientificName",
        "count",
        "lifeStage",
        "classificationMethod",
        "classifiedBy",
        "classificationConfidence",
    ]
    writer.writerow(headers)

    obs_index = 0
    for msg in messages:
        model_out = msg.get("model_output")
        if not model_out or not isinstance(model_out, dict):
            continue

        # Handle single detection or list
        detections = model_out.get("detections", [model_out])
        if not isinstance(detections, list):
            detections = [detections]

        for det in detections:
            obs_index += 1
            detection_class = det.get("detection") or det.get("class") or "unknown"
            confidence = det.get("confidence", "")

            writer.writerow([
                f"obs-{obs_index:06d}",
                msg.get("deployment_id", ""),
                msg.get("id", ""),
                msg.get("id", ""),
                msg.get("received_at", ""),
                msg.get("received_at", ""),
                "animal" if detection_class not in ("person", "vehicle") else "human",
                detection_class,
                det.get("count", 1),
                "",  # lifeStage
                "machineLearning",
                "Wildlife Watcher AI",
                confidence,
            ])

    return output.getvalue()


def _build_datapackage_descriptor(
    org_id: str,
    num_deployments: int,
    num_media: int,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the Frictionless Data Package descriptor."""
    now = datetime.now(timezone.utc).isoformat()

    return {
        "profile": "https://rs.gbif.org/sandbox/data-packages/camtrap-dp/1.0/profile/camtrap-dp-profile.json",
        "name": f"wildlife-watcher-export-{org_id[:8]}",
        "id": org_id,
        "created": now,
        "title": "Wildlife Watcher Camera Trap Data Package",
        "description": f"Exported {num_deployments} deployments with {num_media} events",
        "version": CAMTRAPDP_VERSION,
        "contributors": [
            {
                "title": "Wildlife Watcher",
                "role": "publisher",
                "organization": "wildlife.ai",
            }
        ],
        "sources": [
            {
                "title": "Wildlife Watcher Platform",
                "path": "https://wildlifewatcher.ai",
            }
        ],
        "licenses": [
            {
                "name": "CC-BY-4.0",
                "path": "https://creativecommons.org/licenses/by/4.0/",
                "title": "Creative Commons Attribution 4.0",
            }
        ],
        "temporal": {
            "start": date_from or "",
            "end": date_to or now,
        },
        "taxonomic": [],
        "geographic": {},
        "project": {
            "title": "Wildlife Watcher Monitoring",
        },
        "resources": [
            {
                "name": "deployments",
                "path": "deployments.csv",
                "schema": "https://rs.gbif.org/sandbox/data-packages/camtrap-dp/1.0/table-schemas/deployments.json",
            },
            {
                "name": "media",
                "path": "media.csv",
                "schema": "https://rs.gbif.org/sandbox/data-packages/camtrap-dp/1.0/table-schemas/media.json",
            },
            {
                "name": "observations",
                "path": "observations.csv",
                "schema": "https://rs.gbif.org/sandbox/data-packages/camtrap-dp/1.0/table-schemas/observations.json",
            },
        ],
    }
