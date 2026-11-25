# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pydantic schemas for the Public Data API (/api/v1/*).

These schemas define the external-facing data contract for partner
platforms (Wildlife Insights, TRAPPER, EcoSecrets, GBIF).
"""

import uuid
from datetime import datetime
from typing import Annotated, List, Optional

from pydantic import BaseModel, Field, StringConstraints

from app.schemas.common import ApiResponse
from app.schemas.job import JobStatus

# ── API Key Management ───────────────────────────────────────────────


class ApiKeyCreate(BaseModel):
    """Request to create a new API key."""

    organisation_id: uuid.UUID = Field(..., description="Organisation the key belongs to. The caller must be its organisation_manager.")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] = Field(..., description="Human-readable key name")
    scopes: List[str] = Field(..., min_length=1, description="Permission scopes (e.g. 'deployments:read')")
    expires_at: Optional[datetime] = Field(None, description="The key stops working after this time. Must be in the future.")


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


# ── Devices ──────────────────────────────────────────────────────────


class DeviceOut(BaseModel):
    """A camera with at least one deployment, open or ended and not deleted, in a project of the organisation.

    Cameras belong to no organisation, so a camera lent to another organisation's project is
    in both organisations' lists.
    """

    id: str
    name: str
    bluetooth_id: str
    device_eui: Optional[str] = Field(None, description="The camera's LoRaWAN EUI, which the telemetry endpoint takes")
    created_at: Optional[str] = Field(None, description="When the camera was registered")


class DevicesResponse(ApiResponse):
    data: List[DeviceOut] = Field(default_factory=list)


# ── Telemetry ────────────────────────────────────────────────────────


class TelemetryPoint(BaseModel):
    """One LoRaWAN message a camera sent while deployed in one of the organisation's projects.

    A value is null when the message did not carry it: the camera can send its battery and SD
    card in one message and its health in another.
    """

    timestamp: Optional[str] = Field(None, description="When the network received the message")
    deployment_id: str
    battery_level: Optional[int] = Field(
        None, description="Battery as a percentage of the camera's battery scale, not of charge; older firmware sends only this"
    )
    battery_mv: Optional[int] = Field(None, description="Battery voltage in millivolts")
    sd_free_pct: Optional[int] = Field(None, description="SD card free space, percent; null when the camera could not tell")
    temperature_c: Optional[int] = Field(None, description="The BLE processor's chip temperature, degrees C")
    self_test_bits: Optional[int] = Field(None, description="The camera's self-test bits, 0 when all is well")
    last_reset_reason: Optional[int] = Field(None, description="Why the camera last restarted, as a firmware code")
    reset_count: Optional[int] = Field(None, description="Restarts since the battery was connected, stops at 255")
    hours_since_reset: Optional[int] = Field(None, description="Hours since the camera last restarted, stops at 65535")
    ai_failures: Optional[int] = Field(None, description="Times the AI processor did not respond since the battery was connected, stops at 255")
    model_output: Optional[str] = Field(None, description="The camera's on-device model output, as the payload carried it")


class TelemetryResponse(ApiResponse):
    data: List[TelemetryPoint] = Field(default_factory=list)


# ── Observations: one verdict per photo ──────────────────────────────


class ObservationOut(BaseModel):
    """The verdict the website's photo grid shows for one photo (#170).

    The label fields come from the observation the grid's card shows: a person's verdict
    first, else the consensus of the AI models, else the first observation. They are null
    when no observation names the photo.
    """

    media_id: str
    deployment_id: str
    project_id: str
    timestamp: Optional[str] = Field(None, description="When the photo was taken")
    is_empty: bool = Field(..., description="The photo shows no animal, person or vehicle")
    human_reviewed: bool = Field(..., description="A person has reviewed at least one of the photo's observations")
    observation_id: Optional[str] = Field(None, description="The observation the verdict comes from")
    observation_type: Optional[str] = Field(None, description="animal, human, vehicle, blank or unknown")
    scientific_name: Optional[str] = None
    vernacular_name: Optional[str] = None
    taxon_id: Optional[str] = None
    count: Optional[int] = None
    life_stage: Optional[str] = None
    sex: Optional[str] = None
    behavior: Optional[str] = None
    classification_method: Optional[str] = Field(None, description="human or machine")
    classification_probability: Optional[float] = None
    review_status: Optional[str] = None
    source_type: Optional[str] = Field(None, description="ai, human, imported or consensus")
    ai_origin: Optional[str] = Field(None, description="edge (the camera's model) or cloud, for an AI label")


class ObservationsResponse(ApiResponse):
    data: List[ObservationOut] = Field(default_factory=list)


# ── Jobs ─────────────────────────────────────────────────────────────


class ApiJobOut(BaseModel):
    """An export job's state. ``result_url`` is the signed download link once it has finished."""

    job_id: str
    status: JobStatus
    progress: float = Field(..., description="0.0 to 1.0")
    message: Optional[str] = None
    error: Optional[str] = None
    result_url: Optional[str] = Field(None, description="Signed link to the ZIP, valid for 24 hours from when the job finished")
    created_at: datetime
    updated_at: Optional[datetime] = None


class ApiJobResponse(ApiResponse):
    data: Optional[ApiJobOut] = None
