# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""LoRaWAN webhook payload schemas for TTN, Chirpstack, and generic uplinks."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# ── TTN v3 ────────────────────────────────────────────────────────────


class TTNEndDeviceIds(BaseModel):
    device_id: str
    dev_eui: str
    application_ids: Dict[str, str] = Field(default_factory=dict)


class TTNUplinkMessage(BaseModel):
    frm_payload: str = Field(..., description="Base64-encoded frame payload")
    f_port: Optional[int] = None
    rx_metadata: Optional[List[Dict[str, Any]]] = None
    settings: Optional[Dict[str, Any]] = None


class TTNUplink(BaseModel):
    """The Things Network v3 uplink webhook payload."""

    end_device_ids: TTNEndDeviceIds
    uplink_message: TTNUplinkMessage
    received_at: Optional[str] = None


# ── Chirpstack v4 ────────────────────────────────────────────────────


class ChirpstackDeviceInfo(BaseModel):
    devEui: str
    deviceName: Optional[str] = None
    applicationId: Optional[str] = None
    applicationName: Optional[str] = None


class ChirpstackUplink(BaseModel):
    """Chirpstack v4 uplink webhook payload."""

    deviceInfo: ChirpstackDeviceInfo
    data: str = Field(..., description="Base64-encoded payload")
    fPort: Optional[int] = None
    time: Optional[str] = None


# ── Parsed output ────────────────────────────────────────────────────


class ParsedMessage(BaseModel):
    """Normalised representation after parsing any LoRaWAN uplink."""

    device_eui: str
    battery_level: Optional[float] = None
    sd_card_used_capacity: Optional[float] = None
    model_output: Optional[Dict[str, Any]] = None
    raw_payload_hex: Optional[str] = None
    received_at: datetime = Field(default_factory=datetime.utcnow)
