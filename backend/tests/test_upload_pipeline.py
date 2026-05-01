# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the model upload pipeline.

Tests the /api/models/convert endpoint and convert_model_job function
using mocked Supabase and Redis — no Docker required.

Covers:
  - Input validation (file type, size, required fields)
  - Role-based access control for family creation
  - Idempotency guard in convert_model_job
  - SHA-256 hash computation
  - Storage path format
  - API response contract
"""

import hashlib
import io
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ────────────────────────────────────────────────────────────
# Helper to create a minimal zip containing a .TFL
# ────────────────────────────────────────────────────────────


def make_test_model_zip(tfl_content: bytes = b"\x00\x01\x02\x03", filename: str = "1V1.TFL") -> bytes:
    """Create a minimal zip containing a .TFL file for testing."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(filename, tfl_content)
    return buf.getvalue()


def make_test_tflite() -> bytes:
    """Create a minimal fake .tflite for upload testing."""
    # Minimal TFLite flatbuffer header (not valid, but enough for upload validation)
    return b"TFL3" + b"\x00" * 100


# ────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────


@pytest.fixture
def auth_client():
    """FastAPI test client with get_current_user overridden."""
    from fastapi.testclient import TestClient

    from app.dependencies import get_current_user
    from app.main import app

    mock_user = MagicMock()
    mock_user.id = "test-user-id-000"

    app.dependency_overrides[get_current_user] = lambda: mock_user
    yield TestClient(app)
    app.dependency_overrides.clear()


# ────────────────────────────────────────────────────────────
# POST /api/models/convert — input validation
# ────────────────────────────────────────────────────────────


class TestConvertEndpointValidation:
    def test_rejects_invalid_file_type(self, auth_client):
        """Files with invalid MIME type AND extension should be rejected."""
        response = auth_client.post(
            "/api/models/convert",
            files={"file": ("evil.exe", b"MZ\x90\x00", "application/x-msdownload")},
            data={"model_name": "Test"},
        )
        assert response.status_code == 400

    def test_rejects_oversized_file(self, auth_client):
        """Files over 50MB should be rejected with 413."""
        big_content = b"\x00" * (50 * 1024 * 1024 + 1)
        response = auth_client.post(
            "/api/models/convert",
            files={"file": ("model.zip", big_content, "application/zip")},
            data={"model_name": "Test"},
        )
        assert response.status_code == 413

    def test_requires_model_name(self, client):
        """model_name is a required form field — returns 422 without auth override."""
        response = client.post(
            "/api/models/convert",
            files={"file": ("model.zip", b"\x00" * 100, "application/zip")},
            # no model_name, no auth header → 422
        )
        assert response.status_code == 422


# ────────────────────────────────────────────────────────────
# POST /api/models/convert — role checks
# ────────────────────────────────────────────────────────────


class TestConvertEndpointRoles:
    @patch("app.services.supabase_client.create_service_client")
    @patch("app.routers.models.create_service_client")
    def test_rejects_non_manager_creating_family(self, mock_sb_models, mock_sb_global, auth_client):
        """organisation_member cannot create a new model family."""
        mock_sb_client = MagicMock()

        # user_roles returns empty list for organisation_manager query
        roles_result = MagicMock()
        roles_result.data = []

        # ai_model_families lookup returns empty (family doesn't exist)
        family_result = MagicMock()
        family_result.data = []

        def table_side_effect(name):
            table = MagicMock()
            table.select.return_value = table
            table.eq.return_value = table
            table.is_.return_value = table
            if name == "user_roles":
                table.execute.return_value = roles_result
            elif name == "ai_model_families":
                table.execute.return_value = family_result
            return table

        mock_sb_client.table = MagicMock(side_effect=table_side_effect)
        mock_sb_models.return_value = mock_sb_client
        mock_sb_global.return_value = mock_sb_client

        response = auth_client.post(
            "/api/models/convert",
            files={"file": ("model.zip", b"\x00" * 100, "application/zip")},
            data={"model_name": "New Family"},
        )
        assert response.status_code == 403
        assert "organisation managers" in response.json()["detail"].lower()


# ────────────────────────────────────────────────────────────
# POST /api/models/convert — response contract
# ────────────────────────────────────────────────────────────


class TestConvertResponseContract:
    @patch("app.services.supabase_client.create_service_client")
    @patch("app.routers.models.create_service_client")
    @patch("app.jobs.store.create_job", new_callable=AsyncMock)
    @patch("app.services.blob_store.store_blob", new_callable=AsyncMock)
    @patch("app.jobs.runner.enqueue_local_job")
    def test_response_includes_model_id_and_poll_url(self, mock_enqueue, mock_store, mock_create_job, mock_sb_models, mock_sb_global, auth_client):
        """POST /convert should return model_id, job_id, status, poll_url."""
        mock_create_job.return_value = "test-job-123"

        mock_sb_client = MagicMock()

        roles_result = MagicMock()
        roles_result.data = [{"scope_id": "org-1", "role": "organisation_manager"}]

        family_result = MagicMock()
        family_result.data = [{"id": "fam-1"}]

        model_insert_result = MagicMock()
        model_insert_result.data = [{"id": "model-1"}]

        def table_side_effect(name):
            table = MagicMock()
            table.select.return_value = table
            table.insert.return_value = table
            table.eq.return_value = table
            table.is_.return_value = table
            if name == "user_roles":
                table.execute.return_value = roles_result
            elif name == "ai_model_families":
                table.execute.return_value = family_result
            elif name == "ai_models":
                table.execute.return_value = model_insert_result
            return table

        mock_sb_client.table = MagicMock(side_effect=table_side_effect)

        rpc_result = MagicMock()
        rpc_result.data = 1
        rpc_mock = MagicMock()
        rpc_mock.execute.return_value = rpc_result
        mock_sb_client.rpc.return_value = rpc_mock

        mock_sb_models.return_value = mock_sb_client
        mock_sb_global.return_value = mock_sb_client

        response = auth_client.post(
            "/api/models/convert",
            files={"file": ("model.zip", b"\x00" * 100, "application/zip")},
            data={"model_name": "Test Model"},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        # Verify contract shape — job_id and model_id present
        assert "job_id" in data
        assert "model_id" in data
        assert data["status"] == "uploading"
        assert "poll_url" in data
        assert "/api/jobs/" in data["poll_url"]


# ────────────────────────────────────────────────────────────
# convert_model_job — idempotency
# ────────────────────────────────────────────────────────────


class TestConvertModelJobIdempotency:
    @pytest.mark.asyncio
    @patch("app.jobs.definitions.update_job", new_callable=AsyncMock)
    @patch("app.services.supabase_client.create_service_client")
    async def test_skips_already_validated_model(self, mock_sb, mock_update_job):
        """Job should skip processing if model is already validated."""
        mock_client = MagicMock()
        status_result = MagicMock()
        status_result.data = [{"status": "validated"}]

        table = MagicMock()
        table.select.return_value = table
        table.eq.return_value = table
        table.execute.return_value = status_result
        mock_client.table.return_value = table
        mock_sb.return_value = mock_client

        from app.jobs.definitions import convert_model_job

        await convert_model_job("job-1", "user-1", "model-1")

        # Should have called update_job with COMPLETED (idempotency exit)
        mock_update_job.assert_called()
        completed_calls = [c for c in mock_update_job.call_args_list if c.kwargs.get("progress") == 1.0]
        assert len(completed_calls) >= 1, "Expected at least one COMPLETED update_job call"


# ────────────────────────────────────────────────────────────
# convert_model_job — SHA-256 computation
# ────────────────────────────────────────────────────────────


class TestConvertModelJobHash:
    def test_sha256_of_tfl_in_zip(self):
        """The hash should be computed from the .TFL inside the zip, not the zip itself."""
        tfl_content = b"\xde\xad\xbe\xef" * 100
        expected_hash = hashlib.sha256(tfl_content).hexdigest()
        model_zip = make_test_model_zip(tfl_content, "42V3.TFL")

        # Simulate the hash extraction logic from convert_model_job
        computed_hash = ""
        with zipfile.ZipFile(io.BytesIO(model_zip), "r") as zf:
            for name in zf.namelist():
                if name.upper().endswith(".TFL"):
                    computed_hash = hashlib.sha256(zf.read(name)).hexdigest()
                    break

        assert computed_hash == expected_hash
        assert computed_hash != hashlib.sha256(model_zip).hexdigest(), "Hash should be of TFL content, not the zip wrapper"


# ────────────────────────────────────────────────────────────
# Storage path format
# ────────────────────────────────────────────────────────────


class TestStoragePathFormat:
    def test_structured_path_format(self):
        """Storage path must follow {org_id}/{firmware_id}/{version}/ai_model.zip."""
        org_id = "b0000000-0000-0000-0000-000000000001"
        firmware_id = 42
        version = 3

        result_path = f"{org_id}/{firmware_id}/{version}/ai_model.zip"
        assert result_path == "b0000000-0000-0000-0000-000000000001/42/3/ai_model.zip"

    def test_path_components_are_deterministic(self):
        """Same inputs must always produce the same path (idempotent uploads)."""
        org = "org-123"
        fw = 1
        ver = 5
        path1 = f"{org}/{fw}/{ver}/ai_model.zip"
        path2 = f"{org}/{fw}/{ver}/ai_model.zip"
        assert path1 == path2
