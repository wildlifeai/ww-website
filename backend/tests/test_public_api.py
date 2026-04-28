# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the API key service and Public Data API domain."""

import hashlib

from app.domain.public_api import (
    _build_datapackage_descriptor,
    _build_deployments_csv,
    _build_media_csv,
    _build_observations_csv,
)
from app.services.api_key import (
    KEY_PREFIX,
    VALID_SCOPES,
    generate_api_key,
)


class TestApiKeyGeneration:
    def test_key_has_prefix(self):
        raw_key, key_hash = generate_api_key()
        assert raw_key.startswith(KEY_PREFIX)

    def test_key_length(self):
        raw_key, _ = generate_api_key()
        # ww_live_ + 16 hex chars = "ww_live_" (8) + 16 = 24
        assert len(raw_key) > len(KEY_PREFIX)

    def test_hash_is_sha256(self):
        raw_key, key_hash = generate_api_key()
        expected = hashlib.sha256(raw_key.encode()).hexdigest()
        assert key_hash == expected

    def test_keys_are_unique(self):
        key1, _ = generate_api_key()
        key2, _ = generate_api_key()
        assert key1 != key2

    def test_valid_scopes_exist(self):
        assert "deployments:read" in VALID_SCOPES
        assert "telemetry:read" in VALID_SCOPES
        assert "export:camtrapdp" in VALID_SCOPES
        assert len(VALID_SCOPES) == 6


class TestCamtrapDPDeploymentsCsv:
    def test_basic_deployment(self):
        deployments = [
            {
                "id": "dep-001",
                "location_name": "Forest A",
                "latitude": -36.848,
                "longitude": 174.763,
                "deployment_start": "2026-01-01T00:00:00Z",
                "deployment_end": "2026-03-01T00:00:00Z",
                "camera_model": "Wildlife Watcher v2",
                "camera_height": 1.5,
                "projects": {"name": "Bird Survey"},
            }
        ]
        csv_text = _build_deployments_csv(deployments)
        lines = csv_text.strip().split("\n")
        assert len(lines) == 2  # header + 1 row
        assert "deploymentID" in lines[0]
        assert "dep-001" in lines[1]
        assert "Forest A" in lines[1]

    def test_empty_deployments(self):
        csv_text = _build_deployments_csv([])
        lines = csv_text.strip().split("\n")
        assert len(lines) == 1  # header only

    def test_missing_fields_handled(self):
        deployments = [{"id": "dep-002"}]
        csv_text = _build_deployments_csv(deployments)
        assert "dep-002" in csv_text


class TestCamtrapDPMediaCsv:
    def test_basic_messages(self):
        messages = [
            {
                "id": "msg-001",
                "deployment_id": "dep-001",
                "received_at": "2026-02-15T10:00:00Z",
            },
            {
                "id": "msg-002",
                "deployment_id": "dep-001",
                "received_at": "2026-02-15T11:00:00Z",
            },
        ]
        csv_text = _build_media_csv(messages)
        lines = csv_text.strip().split("\n")
        assert len(lines) == 3  # header + 2 rows
        assert "mediaID" in lines[0]
        assert "msg-001" in lines[1]


class TestCamtrapDPObservationsCsv:
    def test_single_detection(self):
        messages = [
            {
                "id": "msg-001",
                "deployment_id": "dep-001",
                "received_at": "2026-02-15T10:00:00Z",
                "model_output": {"detection": "bird", "confidence": 0.92},
            },
        ]
        csv_text = _build_observations_csv(messages)
        lines = csv_text.strip().split("\n")
        assert len(lines) == 2
        assert "bird" in lines[1]
        assert "0.92" in lines[1]
        assert "machineLearning" in lines[1]

    def test_multiple_detections(self):
        messages = [
            {
                "id": "msg-001",
                "deployment_id": "dep-001",
                "received_at": "2026-02-15T10:00:00Z",
                "model_output": {
                    "detections": [
                        {"class": "bird", "confidence": 0.9},
                        {"class": "person", "confidence": 0.6},
                    ]
                },
            },
        ]
        csv_text = _build_observations_csv(messages)
        lines = csv_text.strip().split("\n")
        assert len(lines) == 3  # header + 2 observations
        assert "animal" in lines[1]  # bird → animal
        assert "human" in lines[2]  # person → human

    def test_no_model_output_skipped(self):
        messages = [
            {"id": "msg-001", "deployment_id": "dep-001", "model_output": None},
        ]
        csv_text = _build_observations_csv(messages)
        lines = csv_text.strip().split("\n")
        assert len(lines) == 1  # header only


class TestCamtrapDPDescriptor:
    def test_descriptor_structure(self):
        desc = _build_datapackage_descriptor(
            org_id="test-org-id",
            num_deployments=5,
            num_media=42,
        )
        assert desc["profile"].startswith("https://rs.gbif.org")
        assert desc["version"] == "1.0"
        assert "wildlife-watcher" in desc["name"]
        assert len(desc["resources"]) == 3
        assert desc["resources"][0]["name"] == "deployments"
        assert desc["resources"][1]["name"] == "media"
        assert desc["resources"][2]["name"] == "observations"
        assert desc["licenses"][0]["name"] == "CC-BY-4.0"

    def test_descriptor_with_dates(self):
        desc = _build_datapackage_descriptor(
            org_id="org-123",
            num_deployments=1,
            num_media=10,
            date_from="2026-01-01",
            date_to="2026-04-01",
        )
        assert desc["temporal"]["start"] == "2026-01-01"
        assert desc["temporal"]["end"] == "2026-04-01"
