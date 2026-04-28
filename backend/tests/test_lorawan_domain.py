# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the LoRaWAN domain — payload parsing and schema validation."""

import base64
import json

import pytest

from app.domain.lorawan import LoRaWANDomain
from app.schemas.lorawan import ChirpstackUplink, ParsedMessage, TTNUplink


class TestWWPayloadParsing:
    """Test the _parse_ww_payload method that parses Wildlife Watcher binary frames."""

    def setup_method(self):
        self.domain = LoRaWANDomain()

    def test_battery_and_sd(self):
        """First two bytes should be battery % and SD usage %."""
        raw = bytes([75, 42])  # 75% battery, 42% SD
        result = self.domain._parse_ww_payload(raw)
        assert result.battery_level == 75.0
        assert result.sd_card_used_capacity == 42.0

    def test_battery_only(self):
        """Single byte: battery only."""
        raw = bytes([100])
        result = self.domain._parse_ww_payload(raw)
        assert result.battery_level == 100.0
        assert result.sd_card_used_capacity is None

    def test_empty_payload(self):
        """Empty payload should return None for all fields."""
        result = self.domain._parse_ww_payload(b"")
        assert result.battery_level is None
        assert result.sd_card_used_capacity is None
        assert result.model_output is None

    def test_with_json_model_output(self):
        """Bytes 2+ should be parsed as JSON model output."""
        model_data = {"detection": "person", "confidence": 0.95}
        raw = bytes([80, 30]) + json.dumps(model_data).encode("utf-8")
        result = self.domain._parse_ww_payload(raw)
        assert result.battery_level == 80.0
        assert result.sd_card_used_capacity == 30.0
        assert result.model_output["detection"] == "person"

    def test_with_binary_model_output(self):
        """Non-JSON bytes 2+ should be returned as hex."""
        raw = bytes([50, 10, 0xFF, 0xAB, 0xCD])
        result = self.domain._parse_ww_payload(raw)
        assert result.model_output is not None
        assert "raw_hex" in result.model_output

    def test_full_battery_range(self):
        """Battery and SD values at boundaries (0 and 100)."""
        result_zero = self.domain._parse_ww_payload(bytes([0, 0]))
        assert result_zero.battery_level == 0.0
        assert result_zero.sd_card_used_capacity == 0.0

        result_max = self.domain._parse_ww_payload(bytes([100, 100]))
        assert result_max.battery_level == 100.0
        assert result_max.sd_card_used_capacity == 100.0

    def test_raw_payload_hex_preserved(self):
        """Raw payload should always be stored as hex string."""
        raw = bytes([75, 42, 0xDE, 0xAD])
        result = self.domain._parse_ww_payload(raw)
        assert result.raw_payload_hex == "4b2adead"


class TestTTNUplinkSchema:
    """Test TTN v3 webhook payload Pydantic validation."""

    def test_valid_ttn_payload(self):
        payload = {
            "end_device_ids": {
                "device_id": "ww-device-01",
                "dev_eui": "0004A30B001F9ACB",
                "application_ids": {"application_id": "wildlife-watcher"},
            },
            "uplink_message": {
                "frm_payload": base64.b64encode(bytes([75, 42])).decode(),
                "f_port": 1,
            },
            "received_at": "2026-04-12T03:00:00Z",
        }
        uplink = TTNUplink(**payload)
        assert uplink.end_device_ids.dev_eui == "0004A30B001F9ACB"
        assert uplink.end_device_ids.device_id == "ww-device-01"

    def test_minimal_ttn_payload(self):
        """Only required fields."""
        payload = {
            "end_device_ids": {
                "device_id": "dev1",
                "dev_eui": "AABBCCDD11223344",
            },
            "uplink_message": {
                "frm_payload": base64.b64encode(b"\x00").decode(),
            },
        }
        uplink = TTNUplink(**payload)
        assert uplink.uplink_message.frm_payload is not None


class TestChirpstackUplinkSchema:
    """Test Chirpstack v4 webhook payload validation."""

    def test_valid_chirpstack_payload(self):
        payload = {
            "deviceInfo": {
                "devEui": "0004A30B001F9ACB",
                "deviceName": "ww-camera-01",
                "applicationId": "app-123",
            },
            "data": base64.b64encode(bytes([80, 55])).decode(),
            "fPort": 1,
        }
        uplink = ChirpstackUplink(**payload)
        assert uplink.deviceInfo.devEui == "0004A30B001F9ACB"

    def test_minimal_chirpstack_payload(self):
        payload = {
            "deviceInfo": {"devEui": "AABBCCDD11223344"},
            "data": base64.b64encode(b"\xff").decode(),
        }
        uplink = ChirpstackUplink(**payload)
        assert uplink.data is not None


class TestParsedMessageSchema:
    def test_full_parsed_message(self):
        msg = ParsedMessage(
            device_eui="0004A30B001F9ACB",
            battery_level=75.0,
            sd_card_used_capacity=42.0,
            model_output={"detection": "bird"},
            raw_payload_hex="4b2a",
        )
        assert msg.device_eui == "0004A30B001F9ACB"
        assert msg.battery_level == 75.0

    def test_nullable_fields(self):
        msg = ParsedMessage(device_eui="test")
        assert msg.battery_level is None
        assert msg.sd_card_used_capacity is None
        assert msg.model_output is None


class TestLoRaWANWebhookSecretValidation:
    """Test webhook secret validation via the router helper."""

    def test_valid_secret_passes(self):
        from app.routers.lorawan import _validate_webhook_secret

        # Should not raise
        _validate_webhook_secret("my-secret", "my-secret")

    def test_invalid_secret_raises(self):
        from app.routers.lorawan import _validate_webhook_secret

        with pytest.raises(Exception):  # HTTPException
            _validate_webhook_secret("wrong", "correct")

    def test_empty_expected_allows_all(self):
        from app.routers.lorawan import _validate_webhook_secret

        # No secret configured — dev mode, should not raise
        _validate_webhook_secret("anything", "")
        _validate_webhook_secret("", "")
