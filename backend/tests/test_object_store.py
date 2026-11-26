# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""services/object_store.py: the Azure delegation, the local directory and S3 through a stubbed client."""

from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber

from app.config import settings
from app.services import object_store


@pytest.fixture
def s3(monkeypatch):
    """S3 backend on a stubbed client with fake credentials, so presigning works offline."""
    client = object_store.new_s3_client(region_name="ap-southeast-2", aws_access_key_id="testing", aws_secret_access_key="testing")
    stub = Stubber(client)
    monkeypatch.setattr(settings, "OBJECT_STORE_BACKEND", "s3")
    monkeypatch.setattr(settings, "S3_BUCKET", "ww-test-media")
    object_store.set_s3_client(client)
    with stub:
        yield stub
    object_store.set_s3_client(None)


@pytest.fixture
def local(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "OBJECT_STORE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_OBJECT_STORE_DIR", str(tmp_path))
    return tmp_path


def test_presigned_urls_use_the_regional_endpoint():
    """Auckland is an opt-in region: the global s3.amazonaws.com host does not serve its buckets."""
    client = object_store.new_s3_client(region_name="ap-southeast-6", aws_access_key_id="testing", aws_secret_access_key="testing")
    post = client.generate_presigned_post("wildlifewatcher-dev-media", "incoming/u1/a.jpg")
    get = client.generate_presigned_url("get_object", Params={"Bucket": "wildlifewatcher-dev-media", "Key": "k"})
    assert post["url"] == "https://wildlifewatcher-dev-media.s3.ap-southeast-6.amazonaws.com/"
    assert get.startswith("https://wildlifewatcher-dev-media.s3.ap-southeast-6.amazonaws.com/k?")


def _body(data: bytes) -> StreamingBody:
    return StreamingBody(io.BytesIO(data), len(data))


# ── defaults keep today's behaviour ──────────────────────────────────


def test_defaults_are_todays_azure_and_drive():
    assert settings.OBJECT_STORE_BACKEND == "azure"
    assert settings.ARCHIVE_BACKEND == "gdrive"
    assert settings.WORKER_BACKEND == "local"


async def test_azure_backend_delegates_to_azure_storage(monkeypatch):
    store, retrieve, delete = AsyncMock(), AsyncMock(return_value=(b"x", {})), AsyncMock()
    monkeypatch.setattr("app.services.azure_storage.store_blob", store)
    monkeypatch.setattr("app.services.azure_storage.retrieve_blob", retrieve)
    monkeypatch.setattr("app.services.azure_storage.delete_blob", delete)

    await object_store.store_blob("k1", b"data", metadata={"a": 1})
    assert await object_store.retrieve_blob("k1") == (b"x", {})
    await object_store.delete_blob("k1")

    store.assert_awaited_once_with("k1", b"data", metadata={"a": 1})
    retrieve.assert_awaited_once_with("k1")
    delete.assert_awaited_once_with("k1")


def test_azure_export_link_uses_the_configured_container(monkeypatch):
    monkeypatch.setattr("app.services.azure_storage.read_link", lambda c, n, ttl, f: f"https://blob/{c}/{n}?{ttl}&{f}")
    assert object_store.export_link("j.zip", 60, "x.zip") == f"https://blob/{settings.CAMTRAPDP_EXPORT_CONTAINER}/j.zip?60&x.zip"


def test_configured_flags_follow_the_backend(monkeypatch):
    monkeypatch.setattr(settings, "OBJECT_STORE_BACKEND", "s3")
    monkeypatch.setattr(settings, "S3_BUCKET", "")
    assert settings.object_store_configured is False
    monkeypatch.setattr(settings, "S3_BUCKET", "b")
    assert settings.object_store_configured is True
    monkeypatch.setattr(settings, "ARCHIVE_BACKEND", "s3")
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_ENABLED", False)
    assert settings.archive_enabled is True
    monkeypatch.setattr(settings, "OBJECT_STORE_BACKEND", "local")
    assert settings.object_store_configured is True


# ── s3:// paths ──────────────────────────────────────────────────────


def test_parse_s3_path_with_and_without_scheme():
    assert object_store.parse_s3_path("s3://b/originals/p/d/h.jpg") == ("b", "originals/p/d/h.jpg")
    assert object_store.parse_s3_path("b/k") == ("b", "k")
    with pytest.raises(object_store.ObjectStoreError):
        object_store.parse_s3_path("s3://bucket-only")


def test_archived_path_prefers_the_s3_path():
    assert object_store.archived_path({"file_id": "abc"}) == "gdrive://abc"
    assert object_store.archived_path({"file_id": "k", "file_path": "s3://b/k"}) == "s3://b/k"


# ── local backend ────────────────────────────────────────────────────


async def test_local_buffer_round_trip(local):
    await object_store.store_blob("blob-1", b"jpeg-bytes")
    assert (local / "buffer" / "blob-1").read_bytes() == b"jpeg-bytes"
    assert await object_store.retrieve_blob("blob-1") == (b"jpeg-bytes", {})
    await object_store.delete_blob("blob-1")
    assert await object_store.retrieve_blob("blob-1") == (None, None)


async def test_local_refuses_a_key_outside_the_root(local):
    with pytest.raises(object_store.ObjectStoreError):
        await object_store.put_object("../escape", b"x")


async def test_local_export_lands_under_exports(local, tmp_path):
    src = tmp_path / "src.zip"
    src.write_bytes(b"PK")
    await object_store.upload_export("job.zip", src)
    assert (local / "exports" / "job.zip").read_bytes() == b"PK"
    assert object_store.export_link("job.zip", 60, "x.zip").startswith("file:")


# ── s3 backend ───────────────────────────────────────────────────────


async def test_s3_buffer_round_trip(s3):
    s3.add_response("put_object", {}, {"Bucket": "ww-test-media", "Key": "buffer/b1", "Body": b"img", "ContentType": ANY, "Metadata": {}})
    s3.add_response("get_object", {"Body": _body(b"img"), "ContentType": "image/jpeg"}, {"Bucket": "ww-test-media", "Key": "buffer/b1"})
    s3.add_response("head_object", {"Metadata": {"filename": "a.zip"}}, {"Bucket": "ww-test-media", "Key": "buffer/b1"})
    s3.add_response("delete_object", {}, {"Bucket": "ww-test-media", "Key": "buffer/b1"})

    await object_store.store_blob("b1", b"img")
    assert await object_store.retrieve_blob("b1") == (b"img", {"filename": "a.zip"})
    await object_store.delete_blob("b1")
    s3.assert_no_pending_responses()


async def test_s3_missing_object_reads_as_none(s3):
    s3.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)
    assert await object_store.retrieve_blob("gone") == (None, None)


def test_presign_get_is_offline_and_scoped(s3):
    url = object_store.presign_get("originals/p/d/h.jpg", 120, filename="photo.jpg")
    assert "ww-test-media" in url and "originals/p/d/h.jpg" in url
    assert "X-Amz-Expires=120" in url and "response-content-disposition" in url


def test_presign_post_caps_the_size(s3):
    post = object_store.presign_post("incoming/u/up/0-a.jpg", 1024, 600, "image/jpeg")
    assert post["fields"]["key"] == "incoming/u/up/0-a.jpg"
    assert post["fields"]["Content-Type"] == "image/jpeg"
    assert "policy" in post["fields"]


async def test_s3_archive_puts_new_files_and_skips_existing(s3):
    from app.services.google_drive import compute_file_hash

    project, deployment = {"id": "p1", "name": "P"}, {"id": "d1"}
    h_new, h_old = compute_file_hash(b"new", "d1"), compute_file_hash(b"old", "d1")
    new_key, old_key = f"originals/p1/d1/{h_new}.jpg", f"originals/p1/d1/{h_old}.jpg"
    files = [
        {"file_bytes": b"new", "filename": "A.JPG", "timestamp": None, "project": project, "deployment": deployment},
        {"file_bytes": b"old", "filename": "B.JPG", "timestamp": None, "project": project, "deployment": deployment},
    ]
    # The two files run concurrently, so the order of the head calls is not fixed: stub by key.
    client = object_store.s3_client()
    heads = {new_key: False, old_key: True}
    puts: list[str] = []

    def head_object(Bucket, Key):  # noqa: N803 - boto3's keyword names
        if not heads[Key]:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {}

    def put_object(Bucket, Key, Body, ContentType):  # noqa: N803
        puts.append(Key)
        return {}

    s3.deactivate()
    client.head_object = head_object
    client.put_object = put_object
    events: list[str] = []

    async def cb(action, **kw):
        events.append(action)

    stats = await object_store.S3Archive().upload_analysis_images(files, file_callback=cb)
    assert (stats["uploaded"], stats["skipped"], stats["failed"]) == (1, 1, 0)
    assert puts == [new_key]
    by_name = {f["file_path"]: f for f in stats["files"]}
    assert by_name[f"s3://ww-test-media/{new_key}"]["was_new"] is True
    assert by_name[f"s3://ww-test-media/{old_key}"]["was_new"] is False
    assert sorted(events) == ["skipped", "uploaded"]


async def test_s3_archive_counts_a_photo_without_a_deployment_as_failed(s3):
    stats = await object_store.S3Archive().upload_analysis_images([{"file_bytes": b"x", "filename": "C.JPG", "project": None}])
    assert stats["failed"] == 1 and stats["files"] == []


def test_archive_service_follows_the_setting(monkeypatch):
    monkeypatch.setattr(settings, "ARCHIVE_BACKEND", "s3")
    assert isinstance(object_store.archive_service(), object_store.S3Archive)


# ── readers of s3:// paths ───────────────────────────────────────────


async def test_resolver_reads_an_s3_original(s3, s3_folders):
    from app.domain.media_resolver import MediaRef, resolve_media

    s3_folders["d"] = {"p"}
    s3.add_response("get_object", {"Body": _body(b"jpeg"), "ContentType": "image/jpeg"}, {"Bucket": "ww-test-media", "Key": "originals/p/d/h.jpg"})
    assert await resolve_media("s3://ww-test-media/originals/p/d/h.jpg", media=MediaRef("m1", "d")) == (b"jpeg", "image/jpeg")


async def test_resolver_returns_none_for_a_missing_s3_original(s3, s3_folders):
    from app.domain.media_resolver import MediaRef, resolve_media

    s3_folders["d"] = {"p"}
    s3.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)
    assert await resolve_media("s3://ww-test-media/originals/p/d/missing.jpg", media=MediaRef("m1", "d")) is None


@pytest.mark.parametrize(
    ("file_path", "readable"),
    [
        ("s3://ww-test-media/originals/p/d/h.jpg", True),
        ("gdrive://abc", True),
        ("https://example.org/a.jpg", True),
        ("http://example.org/a.jpg", True),
        ("supabase://bucket/a.jpg", False),  # the placeholder reads nothing
        ("media/IMG_3.JPG", False),  # imported without its file
        ("", False),
    ],
)
def test_can_resolve_follows_the_resolver_registry(file_path, readable):
    """What the CamtrapDP export fetches: every provider resolve_media has, s3:// included (#493)."""
    from app.domain.media_resolver import can_resolve

    assert can_resolve(file_path) is readable


async def test_the_exports_fetch_reads_an_s3_original(s3, s3_folders):
    from app.domain import camtrapdp_export
    from app.domain.media_resolver import MediaRef

    s3_folders["d"] = {"p"}
    s3.add_response("get_object", {"Body": _body(b"jpeg"), "ContentType": "image/jpeg"}, {"Bucket": "ww-test-media", "Key": "originals/p/d/h.jpg"})
    assert await camtrapdp_export._fetch_original("s3://ww-test-media/originals/p/d/h.jpg", MediaRef("m1", "d")) == b"jpeg"
    s3.assert_no_pending_responses()


async def test_image_route_redirects_an_s3_original(s3, monkeypatch):
    from app.routers import media as media_router

    anon = MagicMock()
    anon.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = SimpleNamespace(
        data={"deployment_id": "d", "file_path": "s3://ww-test-media/originals/p/d/h.jpg"}
    )
    monkeypatch.setattr(media_router.supabase_client, "create_anon_client", lambda: anon)
    monkeypatch.setattr("app.domain.media_resolver._read_projects", lambda ids: {"d": frozenset({"p"})})
    resp = await media_router.get_media_image("m1", size="full", user=SimpleNamespace(id="u1"))
    assert resp.status_code == 307
    assert "originals/p/d/h.jpg" in resp.headers["location"]


async def test_image_route_proxies_s3_bytes_when_the_backend_is_azure(monkeypatch):
    from app.routers import media as media_router

    anon = MagicMock()
    anon.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = SimpleNamespace(
        data={"file_path": "s3://b/k.jpg"}
    )
    monkeypatch.setattr(media_router.supabase_client, "create_anon_client", lambda: anon)
    monkeypatch.setattr("app.domain.media_resolver.resolve_media", AsyncMock(return_value=(b"jpeg", "image/jpeg")))
    resp = await media_router.get_media_image("m1", size="full", user=SimpleNamespace(id="u1"))
    assert resp.status_code == 200 and resp.body == b"jpeg"


# ── originals too large for Lambda's answer (#457) ───────────────────

BIG = b"\xff\xd8" + b"x" * (5 * 1024 * 1024)  # a 5 MB Drive original


async def test_a_large_drive_original_is_served_from_a_copy_in_the_bucket(s3, monkeypatch):
    from app.domain import media_resolver

    monkeypatch.setattr(media_resolver, "resolve_media", AsyncMock(return_value=(BIG, "image/jpeg")))
    key = media_resolver.staged_key("gdrive://abc", "full")
    assert key.startswith("buffer/media/") and "abc" not in key
    s3.add_response("put_object", {}, {"Bucket": "ww-test-media", "Key": key, "Body": BIG, "ContentType": "image/jpeg", "Metadata": {}})
    served = await media_resolver.serve_media("gdrive://abc")
    s3.assert_no_pending_responses()
    assert served and not served.data and key in served.url and "X-Amz-Expires=300" in served.url


async def test_a_small_original_and_any_original_off_s3_are_answered_inline(s3, monkeypatch):
    from app.domain import media_resolver

    monkeypatch.setattr(media_resolver, "resolve_media", AsyncMock(return_value=(b"jpeg", "image/jpeg")))
    assert await media_resolver.serve_media("gdrive://abc") == media_resolver.ServedMedia(data=b"jpeg", content_type="image/jpeg")
    monkeypatch.setattr(settings, "OBJECT_STORE_BACKEND", "azure")  # Azure has no 6 MB answer: unchanged
    monkeypatch.setattr(media_resolver, "resolve_media", AsyncMock(return_value=(BIG, "image/jpeg")))
    served = await media_resolver.serve_media("gdrive://abc")
    assert served and served.data == BIG and not served.url
    s3.assert_no_pending_responses()


async def test_image_route_redirects_a_large_drive_original(monkeypatch):
    from app.domain.media_resolver import ServedMedia
    from app.routers import media as media_router

    anon = MagicMock()
    anon.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = SimpleNamespace(
        data={"file_path": "gdrive://abc"}
    )
    monkeypatch.setattr(media_router.supabase_client, "create_anon_client", lambda: anon)
    monkeypatch.setattr(media_router, "serve_media", AsyncMock(return_value=ServedMedia(url="https://s3/buffer/media/k?sig")))
    resp = await media_router.get_media_image("m1", size="full", user=SimpleNamespace(id="u1"))
    assert resp.status_code == 307 and resp.headers["location"] == "https://s3/buffer/media/k?sig"
