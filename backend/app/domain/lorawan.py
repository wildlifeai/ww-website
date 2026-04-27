# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""LoRaWAN domain — webhook payload parsing and storage.

Normalises TTN/Chirpstack/generic uplinks into a common representation
and inserts into Supabase (lorawan_messages + lorawan_parsed_messages).
Supabase Realtime auto-broadcasts inserts to the mobile app.
"""

import base64
from typing import Any, Dict, Optional

import structlog

from app.schemas.lorawan import (
    ChirpstackUplink,
    ParsedMessage,
    TTNUplink,
)
from app.services.supabase_client import create_service_client

logger = structlog.get_logger()


class LoRaWANDomain:
    """Handles LoRaWAN message ingestion for all network server types."""

    async def process_ttn_uplink(self, payload: TTNUplink) -> ParsedMessage:
        """Parse TTN v3 webhook payload."""
        device_eui = payload.end_device_ids.dev_eui
        raw_bytes = base64.b64decode(payload.uplink_message.frm_payload)
        return await self._process_common(device_eui, raw_bytes, payload.model_dump())

    async def process_chirpstack_uplink(self, payload: ChirpstackUplink) -> ParsedMessage:
        """Parse Chirpstack v4 webhook payload."""
        device_eui = payload.deviceInfo.devEui
        raw_bytes = base64.b64decode(payload.data)
        return await self._process_common(device_eui, raw_bytes, payload.model_dump())

    async def _process_common(self, device_eui: str, raw_bytes: bytes, raw_json: dict) -> ParsedMessage:
        """Common processing: match device, parse payload, store, notify.

        Steps:
        1. Match device_eui → device record in Supabase
        2. Find active deployment for that device
        3. Parse the WW camera binary payload
        4. Insert into lorawan_messages + lorawan_parsed_messages
        5. Supabase Realtime auto-broadcasts the INSERT to subscribed mobile clients
        """
        client = create_service_client()

        # 1. Match device
        device = await self._match_device(client, device_eui)

        # 2. Find active deployment
        deployment = None
        if device:
            deployment = await self._find_active_deployment(client, device["id"])

        # 3. Parse payload
        parsed = self._parse_ww_payload(raw_bytes)

        # 4. Store raw message
        message_data = {
            "device_eui": device_eui,
            "device_id": device["id"] if device else None,
            "deployment_id": deployment["id"] if deployment else None,
            "raw_payload": raw_json,
        }

        try:
            msg_response = client.table("lorawan_messages").insert(message_data).execute()
            message_id = msg_response.data[0]["id"] if msg_response.data else None
        except Exception as e:
            logger.error("lorawan_message_insert_failed", error=str(e))
            message_id = None

        # 5. Store parsed message
        if message_id:
            parsed_data = {
                "lorawan_message_id": message_id,
                "device_id": device["id"] if device else None,
                "battery_level": parsed.battery_level,
                "sd_card_used_capacity": parsed.sd_card_used_capacity,
                "model_output": parsed.model_output,
            }
            try:
                client.table("lorawan_parsed_messages").insert(parsed_data).execute()
            except Exception as e:
                logger.error("lorawan_parsed_insert_failed", error=str(e))

        logger.info(
            "lorawan_uplink_processed",
            device_eui=device_eui,
            battery=parsed.battery_level,
            device_found=device is not None,
        )

        return parsed

    async def _match_device(self, client, device_eui: str) -> Optional[Dict[str, Any]]:
        """Look up a device by its LoRaWAN EUI."""
        try:
            response = client.table("devices").select("id, name, organisation_id").eq("lorawan_device_eui", device_eui).limit(1).execute()
            return response.data[0] if response.data else None
        except Exception as e:
            logger.warning("device_lookup_failed", device_eui=device_eui, error=str(e))
            return None

    async def _find_active_deployment(self, client, device_id: str) -> Optional[Dict[str, Any]]:
        """Find the currently active deployment for a device."""
        try:
            response = (
                client.table("deployments")
                .select("id, project_id")
                .eq("device_id", device_id)
                .is_("deployment_end", "null")
                .order("deployment_start", desc=True)
                .limit(1)
                .execute()
            )
            return response.data[0] if response.data else None
        except Exception as e:
            logger.warning("deployment_lookup_failed", device_id=device_id, error=str(e))
            return None

    def _parse_ww_payload(self, raw_bytes: bytes) -> ParsedMessage:
        """Parse the Wildlife Watcher camera binary payload.

        The firmware sends a compact binary frame:
        - Byte 0: battery level (0–100%)
        - Byte 1: SD card used capacity (0–100%)
        - Bytes 2+: model output (variable length, JSON or binary)

        TODO: finalise the firmware payload spec and update this parser.
        """
        battery = raw_bytes[0] if len(raw_bytes) > 0 else None
        sd_used = raw_bytes[1] if len(raw_bytes) > 1 else None
        model_output = None

        if len(raw_bytes) > 2:
            try:
                import json

                model_output = json.loads(raw_bytes[2:].decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                model_output = {"raw_hex": raw_bytes[2:].hex()}

        return ParsedMessage(
            device_eui="",  # filled by caller
            battery_level=float(battery) if battery is not None else None,
            sd_card_used_capacity=float(sd_used) if sd_used is not None else None,
            model_output=model_output,
            raw_payload_hex=raw_bytes.hex(),
        )
