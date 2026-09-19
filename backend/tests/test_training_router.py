# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Species Brain training endpoints: the flag gate, the org-manager gate, the per-image
access check, and what gets queued in each trainer mode."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.dependencies import get_current_user, get_verified_user
from app.main import app
from app.middleware.rate_limit import limiter

USER = SimpleNamespace(id="user-1", email="t@ww.ai", email_confirmed_at="2026-01-01T00:00:00Z", app_metadata={})
MEDIA_IDS = [f"00000000-0000-0000-0000-0000000000{i:02d}" for i in range(1, 5)]
ROLES = [{"scope_id": "org-1", "role": "organisation_manager"}]


def _body(**over):
    body = {
        "media_ids": MEDIA_IDS,
        "model_name": "Rat brain",
        "classes": [{"label": "rat", "scientific_name": "Rattus rattus"}],
    }
    body.update(over)
    return body


@pytest.fixture
def auth_client(client):
    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[get_verified_user] = lambda: USER
    limiter.reset()  # every test POSTs from the same test-client address
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_verified_user, None)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "FF_MODEL_TRAINING_ENABLED", True)
    monkeypatch.setattr(settings, "EDGE_IMPULSE_API_KEY", "")
    monkeypatch.setattr(settings, "EDGE_IMPULSE_PROJECT_ID", 0)


def test_status_reports_flag_mode_and_limits(auth_client, monkeypatch):
    monkeypatch.setattr(settings, "FF_MODEL_TRAINING_ENABLED", False)
    monkeypatch.setattr(settings, "EDGE_IMPULSE_API_KEY", "")
    r = auth_client.get("/api/models/train/status")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["enabled"] is False
    assert data["mode"] == "export_only"
    assert data["max_classes"] <= 16
    assert data["image_sizes"] == [96, 160]


def test_train_disabled_returns_feature_envelope(auth_client, monkeypatch):
    monkeypatch.setattr(settings, "FF_MODEL_TRAINING_ENABLED", False)
    r = auth_client.post("/api/models/train", json=_body())
    assert r.status_code == 200
    assert r.json()["error"]["code"] == "FEATURE_DISABLED"


def test_train_validates_body(auth_client, enabled):
    r = auth_client.post("/api/models/train", json=_body(classes=[]))
    assert r.status_code == 422
    r = auth_client.post("/api/models/train", json=_body(image_size=128))
    assert r.status_code == 422


def test_train_requires_org_manager(auth_client, enabled):
    with patch("app.routers.models.get_manager_roles", AsyncMock(return_value=[])):
        r = auth_client.post("/api/models/train", json=_body())
    assert r.status_code == 403


def test_train_rejects_missing_images(auth_client, enabled):
    found = {m: "dep-1" for m in MEDIA_IDS[:-1]}  # one selected image is gone
    with (
        patch("app.routers.models.get_manager_roles", AsyncMock(return_value=ROLES)),
        patch("app.routers.models.create_service_client", lambda: MagicMock()),
        patch("app.routers.models.media_deployments", AsyncMock(return_value=found)),
    ):
        r = auth_client.post("/api/models/train", json=_body())
    assert r.status_code == 400
    assert "1 of the selected images" in r.json()["detail"]


def test_train_rejects_images_the_caller_cannot_read(auth_client, enabled):
    found = {m: ("dep-1" if i % 2 else "dep-2") for i, m in enumerate(MEDIA_IDS)}
    with (
        patch("app.routers.models.get_manager_roles", AsyncMock(return_value=ROLES)),
        patch("app.routers.models.create_service_client", lambda: MagicMock()),
        patch("app.routers.models.media_deployments", AsyncMock(return_value=found)),
        patch("app.routers.models.accessible_deployment_ids", AsyncMock(return_value=["dep-1"])) as access,
    ):
        r = auth_client.post("/api/models/train", json=_body())
    assert r.status_code == 403
    assert sorted(access.await_args.args[1]) == ["dep-1", "dep-2"]


def test_train_export_only_queues_job_without_model_row(auth_client, enabled):
    found = {m: "dep-1" for m in MEDIA_IDS}
    enqueue = AsyncMock(return_value="local")
    with (
        patch("app.routers.models.get_manager_roles", AsyncMock(return_value=ROLES)),
        patch("app.routers.models.create_service_client", lambda: MagicMock()),
        patch("app.routers.models.media_deployments", AsyncMock(return_value=found)),
        patch("app.routers.models.accessible_deployment_ids", AsyncMock(return_value=["dep-1"])),
        patch("app.routers.models.create_job", AsyncMock(return_value="job-1")) as create,
        patch("app.routers.models.enqueue_job", enqueue),
    ):
        # A duplicated id is folded away before anything is queued.
        r = auth_client.post("/api/models/train", json=_body(media_ids=MEDIA_IDS + [MEDIA_IDS[0]]))
    assert r.status_code == 200
    assert r.json()["data"] == {"job_id": "job-1", "model_id": None, "mode": "export_only", "status": "queued", "poll_url": "/api/jobs/job-1"}
    assert create.await_args.kwargs["kind"] == "model_train"
    name, job_id, user_id, model_id, org_id, params = enqueue.await_args.args
    assert (name, job_id, user_id, model_id, org_id) == ("train_species_brain_job", "job-1", "user-1", None, "org-1")
    assert params["media_ids"] == MEDIA_IDS
    assert params["organisation_id"] == "org-1"
    assert params["classes"][0]["scientific_name"] == "Rattus rattus"


def test_train_with_edge_impulse_creates_the_model_row_first(auth_client, enabled, monkeypatch):
    monkeypatch.setattr(settings, "EDGE_IMPULSE_API_KEY", "ei_test")
    monkeypatch.setattr(settings, "EDGE_IMPULSE_PROJECT_ID", 4242)
    found = {m: "dep-1" for m in MEDIA_IDS}

    table = MagicMock()
    table.insert.return_value = table
    table.select.return_value = table
    table.execute.return_value = MagicMock(data=[{"id": "model-9"}])
    svc = MagicMock()
    svc.table.return_value = table

    enqueue = AsyncMock(return_value="arq")
    with (
        patch("app.routers.models.get_manager_roles", AsyncMock(return_value=ROLES)),
        patch("app.routers.models.create_service_client", lambda: svc),
        patch("app.routers.models.media_deployments", AsyncMock(return_value=found)),
        patch("app.routers.models.accessible_deployment_ids", AsyncMock(return_value=["dep-1"])),
        patch("app.routers.models.resolve_or_create_model_family", AsyncMock(return_value=("fam-1", 12))),
        patch("app.routers.models.next_model_version", AsyncMock(return_value=(2, "2.0.0-abc123"))),
        patch("app.routers.models.create_job", AsyncMock(return_value="job-2")),
        patch("app.routers.models.enqueue_job", enqueue),
    ):
        r = auth_client.post("/api/models/train", json=_body(organisation_id="org-1", description="first go"))
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["mode"] == "edge_impulse"
    assert data["model_id"] == "model-9"
    inserted = table.insert.call_args.args[0]
    assert inserted["model_family_id"] == "fam-1"
    assert inserted["version"] == "2.0.0-abc123"
    assert inserted["version_number"] == 2
    assert inserted["file_type"] == "training"
    assert inserted["model_path"] is None
    assert enqueue.await_args.args[3] == "model-9"
