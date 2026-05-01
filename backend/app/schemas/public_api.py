# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pydantic schemas for the Public Data API (/api/v1/*).

These schemas define the external-facing data contract for partner
platforms (Wildlife Insights, TRAPPER, EcoSecrets, GBIF).
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# ── API Key Management ───────────────────────────────────────────────


class ApiKeyCreate(BaseModel):
    """Request to create a new API key."""

    name: str = Field(..., description="Human-readable key name", max_length=100)
    scopes: List[str] = Field(..., description="Permission scopes (e.g. 'deployments:read')")
    expires_at: Optional[str] = Field(None, description="ISO timestamp — key expires after this time")


class ApiKeyResponse(BaseModel):
    """Returned when a new API key is created. Raw key shown only once."""

    id: str
    name: str
    key: str = Field(..., description="The raw API key — save it now, you won't see it again")
    key_prefix: str
    scopes: List[str]
    expires_at: Optional[str] = None
    created_at: Optional[str] = None


class ApiKeyInfo(BaseModel):
    """API key metadata (no secret). Returned by list endpoint."""

    id: str
    name: str
    key_prefix: str
    scopes: List[str]
    expires_at: Optional[str] = None
    last_used_at: Optional[str] = None
    created_at: Optional[str] = None


# ── Deployment ───────────────────────────────────────────────────────


class DeploymentOut(BaseModel):
    """Deployment record for external consumption."""

    id: str
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    device_id: Optional[str] = None
    device_name: Optional[str] = None
    location_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    deployment_start: Optional[str] = None
    deployment_end: Optional[str] = None
    camera_model: Optional[str] = None
    camera_height: Optional[float] = None
    capture_method: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None


# ── Device ───────────────────────────────────────────────────────────


class DeviceOut(BaseModel):
    """Device record for external consumption."""

    id: str
    name: Optional[str] = None
    bluetooth_id: Optional[str] = None
    lorawan_device_eui: Optional[str] = None
    organisation_id: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[str] = None


# ── Telemetry ────────────────────────────────────────────────────────


class TelemetryPoint(BaseModel):
    """A single telemetry data point from LoRaWAN."""

    timestamp: str
    battery_level: Optional[float] = None
    sd_card_used_capacity: Optional[float] = None
    model_output: Optional[Dict[str, Any]] = None


# ── Observations (AI detections) ─────────────────────────────────────


class ObservationOut(BaseModel):
    """An AI detection observation from LoRaWAN model output."""

    id: str
    device_eui: Optional[str] = None
    deployment_id: Optional[str] = None
    detection_class: Optional[str] = None
    confidence: Optional[float] = None
    timestamp: Optional[str] = None
    raw_output: Optional[Dict[str, Any]] = None


# ── CamtrapDP Export ─────────────────────────────────────────────────


class CamtrapDPExportRequest(BaseModel):
    """Request to generate a CamtrapDP data package."""

    project_id: Optional[str] = Field(None, description="Filter by project")
    deployment_ids: Optional[List[str]] = Field(None, description="Specific deployments to include")
    date_from: Optional[str] = Field(None, description="Start date (ISO)")
    date_to: Optional[str] = Field(None, description="End date (ISO)")
    include_observations: bool = Field(True, description="Include AI detection observations")


# ── Query Parameters ─────────────────────────────────────────────────


class PaginationParams(BaseModel):
    """Standard pagination parameters."""

    limit: int = Field(50, ge=1, le=200, description="Number of records per page")
    offset: int = Field(0, ge=0, description="Offset for pagination")
    order_by: str = Field("created_at", description="Field to sort by")
    order_dir: str = Field("desc", description="Sort direction: 'asc' or 'desc'")
