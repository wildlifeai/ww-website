# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Uploads straight to S3: the presign route, reading keys back, and the endpoints' wiring."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber
from fastapi.testclient import TestClient

from app.config import settings
from app.dependencies import get_current_user
from app.services import incoming, object_store

USER = SimpleNamespace(id="u0000000-0000-4000-8000-000000000001", email="t@wildlife.ai", app_metadata={}, user_metadata={})


@pytest.fixture
def s3(monkeypatch):
    client = object_store.new_s3_client(region_name="ap-southeast-2", aws_access_key_id="testing", aws_secret_access_key="testing")
    monkeypatch.setattr(settings, "OBJECT_STORE_BACKEND", "s3")
    monkeypatch.setattr(settings, "S3_BUCKET", "ww-test-media")
    object_store.set_s3_client(client)
    stub = Stubber(client)
    with stub:
        yield stub
    object_store.set_s3_client(None)


@pytest.fixture
def api():
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: USER
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_config_says_multipart_until_the_store_is_s3(api, monkeypatch):
    assert api.get("/api/uploads/config").json()["mode"] == "multipart"
    monkeypatch.setattr(settings, "OBJECT_STORE_BACKEND", "s3")
    body = api.get("/api/uploads/config").json()
    assert body["mode"] == "s3" and body["max_bytes"]["photos"] == settings.GOOGLE_DRIVE_MAX_FILE_SIZE_MB * 1024 * 1024


def test_presign_is_off_without_s3(api):
    resp = api.post("/api/uploads/presign", json={"purpose": "photos", "files": [{"name": "a.jpg", "size": 10}]})
    assert resp.status_code == 404


def test_presign_hands_out_posts_under_the_callers_prefix(api, s3):
    resp = api.post(
        "/api/uploads/presign",
        json={
            "purpose": "photos",
            "files": [{"name": "DCIM/IMAGE001.JPG", "size": 500, "content_type": "image/jpeg"}, {"name": "b c.jpg", "size": 9}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    first, second = body["files"]
    assert first["key"] == f"incoming/{USER.id}/{body['upload_id']}/0-IMAGE001.JPG"
    assert second["key"].endswith("/1-b c.jpg")
    assert first["fields"]["key"] == first["key"] and "policy" in first["fields"] and first["url"].startswith("https://")


def test_presign_refuses_a_file_over_the_cap(api, s3):
    big = settings.GOOGLE_DRIVE_MAX_FILE_SIZE_MB * 1024 * 1024 + 1
    resp = api.post("/api/uploads/presign", json={"purpose": "photos", "files": [{"name": "huge.jpg", "size": big}]})
    assert resp.status_code == 413 and "huge.jpg" in resp.json()["detail"]


def test_safe_name_keeps_the_last_segment_only():
    from app.routers.uploads import safe_name

    assert safe_name("../../etc/passwd") == "passwd"
    assert safe_name("C:\\cards\\IMG 1.jpg") == "IMG 1.jpg"
    assert safe_name("...") == "file"


# ── reading keys back ────────────────────────────────────────────────


async def test_no_keys_returns_the_body_files():
    assert await incoming.incoming_files(None, ["f"], []) == ["f"]


async def test_keys_need_a_user_and_the_s3_store(monkeypatch):
    with pytest.raises(incoming.IncomingError) as anon:
        await incoming.incoming_files(None, [], ["incoming/x/1/0-a.jpg"])
    assert anon.value.status == 401
    with pytest.raises(incoming.IncomingError) as not_s3:
        await incoming.incoming_files(USER.id, [], [f"incoming/{USER.id}/1/0-a.jpg"])
    assert not_s3.value.status == 400


async def test_a_key_of_another_user_is_refused(s3):
    for key in ("incoming/someone-else/1/0-a.jpg", f"incoming/{USER.id}/../other/0-a.jpg", "originals/p/d/h.jpg"):
        with pytest.raises(incoming.IncomingError) as exc:
            await incoming.incoming_files(USER.id, [], [key])
        assert exc.value.status == 403


async def test_keys_become_files_after_the_body_files(s3):
    key = f"incoming/{USER.id}/up1/3-IMAGE003.JPG"
    s3.add_response(
        "get_object", {"Body": StreamingBody(io.BytesIO(b"jpeg"), 4), "ContentType": "image/jpeg"}, {"Bucket": "ww-test-media", "Key": key}
    )
    out = await incoming.incoming_files(USER.id, ["body-file"], [key])
    assert out[0] == "body-file"
    f = out[1]
    assert (f.filename, f.content_type, f.size, await f.read()) == ("IMAGE003.JPG", "image/jpeg", 4, b"jpeg")


async def test_a_key_with_nothing_behind_it_is_a_404(s3):
    s3.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)
    with pytest.raises(incoming.IncomingError) as exc:
        await incoming.incoming_files(USER.id, [], [f"incoming/{USER.id}/up1/0-a.jpg"])
    assert exc.value.status == 404


# ── the endpoints' wiring ────────────────────────────────────────────


def test_parse_refuses_keys_from_an_anonymous_caller():
    from app.main import app

    resp = TestClient(app).post("/api/exif/parse", data={"files_key": "incoming/x/1/0-a.jpg"})
    assert resp.status_code == 401


def test_parse_needs_a_file_one_way_or_the_other():
    from app.main import app

    assert TestClient(app).post("/api/exif/parse", data={"paths": "a.jpg"}).status_code == 422
