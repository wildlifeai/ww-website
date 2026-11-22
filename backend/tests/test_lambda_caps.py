# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Request caps that keep a route inside Lambda's 6 MB answer and 30 s (#457), and only there.

``POST /api/brain/clusters/multi`` refuses a selection of more photos than its answer can carry,
and ``POST /api/exif/parse`` takes 25 photos a call, counted before any upload key is read.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.dependencies import get_current_user
from app.main import app
from app.routers import brain, exif
from app.services.incoming import IncomingError
from tests.fake_postgrest import FakeDB

USER = SimpleNamespace(id="u0000000-0000-4000-8000-000000000001", email="t@wildlife.ai", app_metadata={}, user_metadata={})
DEPS = ["d0000000-0000-4000-8000-000000000001", "d0000000-0000-4000-8000-000000000002"]
WHEN = "2026-11-01T00:00:00+00:00"
LAMBDA_ANSWER_MAX = 6 * 1024 * 1024


# ── clusters/multi ───────────────────────────────────────────────────


def _member(n: int) -> dict:
    row = {"media_id": f"m{n}", "deployment_id": DEPS[n % 2], "embedding_run_id": f"r{n % 2}", "cluster_id": 0}
    return {**row, "cluster_confidence": 0.9, "is_outlier": False}


def _run(n: int, deployment_id: str) -> dict:
    return {"id": f"r{n}", "deployment_id": deployment_id, "model_name": "dinov3-vits", "status": "complete", "created_at": WHEN}


@pytest.fixture
def multi(monkeypatch):
    """Two deployments, each with a complete run, one cluster and three member photos."""
    db = FakeDB(
        {
            "embedding_runs": [_run(n, d) for n, d in enumerate(DEPS)],
            "cluster_assignments": [
                {"id": f"c{n}", "deployment_id": d, "embedding_run_id": f"r{n}", "cluster_id": 0, "image_count": 3, "created_at": WHEN}
                for n, d in enumerate(DEPS)
            ],
            "media_embeddings": [_member(n) for n in range(6)],
        }
    )
    monkeypatch.setattr(brain, "create_service_client", lambda: db)
    monkeypatch.setattr(brain, "accessible_deployment_ids", AsyncMock(side_effect=lambda user_id, ids: ids))
    monkeypatch.setattr(settings, "FF_WILDLIFE_BRAIN_ENABLED", True)
    monkeypatch.setattr(brain, "MULTI_CLUSTER_MAX_PHOTOS", 5)
    app.dependency_overrides[get_current_user] = lambda: USER
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)


def _multi(client: TestClient) -> dict:
    resp = client.post("/api/brain/clusters/multi", json={"deployment_ids": DEPS})
    assert resp.status_code == 200
    return resp.json()


def test_clusters_multi_refuses_a_selection_over_the_cap_on_lambda(multi, monkeypatch):
    monkeypatch.setattr(settings, "WW_LAMBDA_ROLE", "api")
    body = _multi(multi)
    assert body["error"]["code"] == "TOO_MANY_PHOTOS" and "this selection has 6" in body["error"]["message"]


def test_clusters_multi_answers_every_photo_within_the_cap_or_off_lambda(multi, monkeypatch):
    monkeypatch.setattr(settings, "WW_LAMBDA_ROLE", "")  # Azure: no cap, as before
    assert len(_multi(multi)["data"]["media_clusters"]) == 6
    monkeypatch.setattr(settings, "WW_LAMBDA_ROLE", "api")
    monkeypatch.setattr(brain, "MULTI_CLUSTER_MAX_PHOTOS", 6)
    assert len(_multi(multi)["data"]["media_clusters"]) == 6


def test_the_cap_keeps_the_answer_well_under_6_mb():
    """At the cap every photo in a cluster, the largest form, wrapped and escaped as Lambda answers it."""
    members = {str(uuid.uuid4()): 1234 for _ in range(brain.MULTI_CLUSTER_MAX_PHOTOS)}
    body = {"data": {"clusters": [], "media_clusters": members, "outlier_media_ids": [], "model_groups": []}, "error": None, "meta": {}}
    assert len(json.dumps(json.dumps(body, separators=(",", ":")))) < LAMBDA_ANSWER_MAX * 0.6


# ── exif/parse ───────────────────────────────────────────────────────


@pytest.fixture
def parse(monkeypatch):
    """The parse route with a signed-in caller and the key reader replaced, to see whether it is reached."""
    monkeypatch.setattr(exif, "get_optional_user", AsyncMock(return_value=USER))
    reader = AsyncMock(side_effect=IncomingError(404, "reached the key reader"))
    monkeypatch.setattr(exif, "incoming_files", reader)
    return TestClient(app), reader


def _keys(n: int) -> dict:
    return {"files_key": [f"incoming/{USER.id}/up1/{i}-a.jpg" for i in range(n)]}


def test_parse_on_lambda_refuses_26_photos_before_reading_a_key(parse, monkeypatch):
    client, reader = parse
    monkeypatch.setattr(settings, "WW_LAMBDA_ROLE", "api")
    resp = client.post("/api/exif/parse", data=_keys(26))
    assert resp.status_code == 403
    error = resp.json()["error"]
    assert error["code"] == "IMAGE_LIMIT_EXCEEDED" and "up to 25 images" in error["message"] and "You sent 26" in error["message"]
    reader.assert_not_awaited()


def test_parse_on_lambda_takes_25_and_azure_keeps_its_cap(parse, monkeypatch):
    client, reader = parse
    monkeypatch.setattr(settings, "WW_LAMBDA_ROLE", "api")
    assert client.post("/api/exif/parse", data=_keys(25)).status_code == 404  # past the cap, at the reader
    monkeypatch.setattr(settings, "WW_LAMBDA_ROLE", "")
    assert client.post("/api/exif/parse", data=_keys(26)).status_code == 404
    assert reader.await_count == 2


def test_parse_on_lambda_does_not_ask_an_anonymous_caller_to_log_in_for_more(parse, monkeypatch):
    client, _ = parse
    monkeypatch.setattr(exif, "get_optional_user", AsyncMock(return_value=None))
    monkeypatch.setattr(settings, "WW_LAMBDA_ROLE", "api")
    files = [("files", (f"{i}.jpg", b"jpeg", "image/jpeg")) for i in range(26)]
    message = client.post("/api/exif/parse", files=files).json()["error"]["message"]
    assert "up to 25 images" in message and "log in" not in message
