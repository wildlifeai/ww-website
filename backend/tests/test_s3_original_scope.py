# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""An ``s3://`` original is read only from its own ``originals/<project>/<deployment>/`` folder in
``S3_BUCKET``; any other path is answered as a missing original, by every reader."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from structlog.testing import capture_logs

from app.config import settings
from app.domain import media_resolver
from app.domain.media_resolver import MediaRef, in_own_folder, original_projects, resolve_media, serve_media
from app.services import object_store
from tests.fake_postgrest import FakeDB

BUCKET = "ww-test-media"
PROJECT = "11111111-1111-4111-8111-111111111111"
EARLIER_PROJECT = "22222222-2222-4222-8222-222222222222"
OTHER_PROJECT = "33333333-3333-4333-8333-333333333333"
DEP = "d0000000-0000-4000-8000-000000000001"
OTHER_DEP = "d0000000-0000-4000-8000-000000000002"
HASH = "a" * 64
OWN = f"s3://{BUCKET}/originals/{PROJECT}/{DEP}/{HASH}.jpg"
ROW = MediaRef("m1", DEP)

NOT_ITS_OWN = {
    "another deployment's original": f"s3://{BUCKET}/originals/{PROJECT}/{OTHER_DEP}/{HASH}.jpg",
    "another project's folder": f"s3://{BUCKET}/originals/{OTHER_PROJECT}/{DEP}/{HASH}.jpg",
    "another project's original": f"s3://{BUCKET}/originals/{OTHER_PROJECT}/{OTHER_DEP}/{HASH}.jpg",
    "an export": f"s3://{BUCKET}/exports/job.zip",
    "an upload in incoming": f"s3://{BUCKET}/incoming/u1/0-a.jpg",
    "a results part": f"s3://{BUCKET}/results/L1/J1/part-0001.jsonl.gz",
    "another bucket": f"s3://other-bucket/originals/{PROJECT}/{DEP}/{HASH}.jpg",
    "a key with ..": f"s3://{BUCKET}/originals/{PROJECT}/{DEP}/../{OTHER_DEP}/{HASH}.jpg",
    "a key with a leading slash": f"s3://{BUCKET}//originals/{PROJECT}/{DEP}/{HASH}.jpg",
    "the folder itself": f"s3://{BUCKET}/originals/{PROJECT}/{DEP}/",
}


@pytest.fixture
def folders(s3_folders):
    s3_folders[DEP] = {PROJECT}
    s3_folders[OTHER_DEP] = {PROJECT}
    return s3_folders


@pytest.fixture
def reads(monkeypatch):
    """The S3 reads that reach the object store."""
    get = AsyncMock(return_value=(b"jpeg", "image/jpeg"))
    monkeypatch.setattr(object_store, "get_object", get)
    return get


# ── the rule ─────────────────────────────────────────────────────────


def test_the_rows_own_original_is_in_its_folder(folders):
    assert in_own_folder(OWN, DEP, {PROJECT})


@pytest.mark.parametrize("path", list(NOT_ITS_OWN.values()), ids=list(NOT_ITS_OWN))
def test_any_other_path_is_not(folders, path):
    assert not in_own_folder(path, DEP, {PROJECT})


def test_a_moved_deployment_keeps_the_folder_of_its_earlier_project(folders):
    moved = f"s3://{BUCKET}/originals/{EARLIER_PROJECT}/{DEP}/{HASH}.jpg"
    assert in_own_folder(moved, DEP, {PROJECT, EARLIER_PROJECT})
    assert not in_own_folder(moved, DEP, {PROJECT})


def test_nothing_is_in_its_folder_without_the_bucket_setting(folders, monkeypatch):
    monkeypatch.setattr(settings, "S3_BUCKET", "")
    assert not in_own_folder(OWN, DEP, {PROJECT})
    assert not in_own_folder(OWN, "", {PROJECT})


def test_a_deployments_projects_include_those_it_was_moved_from(monkeypatch):
    db = FakeDB(
        {
            "deployments": [{"id": DEP, "project_id": PROJECT}, {"id": OTHER_DEP, "project_id": OTHER_PROJECT}],
            "deployment_moves": [{"id": "mv1", "deployment_id": DEP, "from_project_id": EARLIER_PROJECT, "to_project_id": PROJECT}],
        }
    )
    monkeypatch.setattr("app.services.supabase_client.create_service_client", lambda: db)
    found = media_resolver._read_projects([DEP, OTHER_DEP, "d-gone"])
    assert found == {DEP: frozenset({PROJECT, EARLIER_PROJECT}), OTHER_DEP: frozenset({OTHER_PROJECT}), "d-gone": frozenset()}


async def test_a_deployments_projects_are_read_once_a_minute(monkeypatch):
    calls = []
    monkeypatch.setattr(media_resolver, "_read_projects", lambda ids: calls.append(list(ids)) or {d: frozenset({PROJECT}) for d in ids})
    assert await original_projects([DEP, DEP, ""]) == {DEP: frozenset({PROJECT})}
    assert await original_projects([DEP]) == {DEP: frozenset({PROJECT})}
    assert calls == [[DEP]]
    monkeypatch.setattr(media_resolver, "FOLDERS_TTL_SECONDS", 0.0)
    await original_projects([DEP])
    assert calls == [[DEP], [DEP]]


# ── resolve_media and serve_media ────────────────────────────────────


async def test_the_rows_own_original_is_read(folders, reads):
    assert await resolve_media(OWN, media=ROW) == (b"jpeg", "image/jpeg")
    reads.assert_awaited_once_with(f"originals/{PROJECT}/{DEP}/{HASH}.jpg", bucket=BUCKET)


@pytest.mark.parametrize("path", list(NOT_ITS_OWN.values()), ids=list(NOT_ITS_OWN))
async def test_any_other_path_is_answered_as_missing_and_never_read(folders, reads, path):
    with capture_logs() as logs:
        assert await resolve_media(path, media=ROW) is None
    reads.assert_not_awaited()
    (entry,) = [e for e in logs if e["event"] == "media_s3_original_refused"]
    assert entry["log_level"] == "warning" and entry["media_id"] == "m1"
    assert path not in str(logs)


async def test_an_s3_path_without_its_row_is_not_read(folders, reads):
    assert await resolve_media(OWN) is None
    assert await resolve_media(OWN, media=MediaRef("m1", "")) is None
    reads.assert_not_awaited()


async def test_an_original_whose_folder_cannot_be_checked_is_not_read(s3_folders, reads, monkeypatch):
    def unreachable(ids):
        raise ConnectionError("Server disconnected")

    monkeypatch.setattr(media_resolver, "_read_projects", unreachable)
    assert await resolve_media(OWN, media=ROW) is None
    reads.assert_not_awaited()


async def test_a_moved_deployments_original_is_still_read(s3_folders, reads):
    s3_folders[DEP] = {PROJECT, EARLIER_PROJECT}
    moved = f"s3://{BUCKET}/originals/{EARLIER_PROJECT}/{DEP}/{HASH}.jpg"
    assert await resolve_media(moved, media=ROW) == (b"jpeg", "image/jpeg")


async def test_other_providers_are_unchanged(folders, monkeypatch):
    drive = AsyncMock(return_value=(b"drive", "image/jpeg"))
    monkeypatch.setitem(media_resolver.RESOLVERS, "gdrive://", (True, drive))
    assert await resolve_media("gdrive://abc") == (b"drive", "image/jpeg")


@pytest.fixture
def on_s3(folders, monkeypatch):
    """The S3 object store, with presigned GETs recorded."""
    monkeypatch.setattr(settings, "OBJECT_STORE_BACKEND", "s3")
    signed = []
    monkeypatch.setattr(
        object_store, "presign_get", lambda key, ttl=None, bucket=None, filename=None: signed.append((bucket, key)) or f"https://s3/{key}"
    )
    return signed


async def test_serve_media_signs_only_the_rows_own_original(on_s3, reads):
    assert await serve_media(OWN, media=ROW) == media_resolver.ServedMedia(url=f"https://s3/originals/{PROJECT}/{DEP}/{HASH}.jpg")
    for path in NOT_ITS_OWN.values():
        assert await serve_media(path, media=ROW) is None
    assert on_s3 == [(BUCKET, f"originals/{PROJECT}/{DEP}/{HASH}.jpg")]
    reads.assert_not_awaited()


# ── the readers ──────────────────────────────────────────────────────


def _anon_row(row: dict) -> MagicMock:
    anon = MagicMock()
    anon.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = SimpleNamespace(data=row)
    return anon


async def test_image_route_answers_another_folders_original_as_not_found(on_s3, monkeypatch):
    from app.routers import media as media_router

    path = NOT_ITS_OWN["another deployment's original"]
    monkeypatch.setattr(media_router.supabase_client, "create_anon_client", lambda: _anon_row({"deployment_id": DEP, "file_path": path}))
    with pytest.raises(HTTPException) as refused:
        await media_router.get_media_image("m1", size="full", user=SimpleNamespace(id="u1"))
    assert refused.value.status_code == 404 and on_s3 == []

    monkeypatch.setattr(media_router.supabase_client, "create_anon_client", lambda: _anon_row({"deployment_id": DEP, "file_path": OWN}))
    resp = await media_router.get_media_image("m1", size="full", user=SimpleNamespace(id="u1"))
    assert resp.status_code == 307


async def test_a_lease_signs_only_originals_in_their_own_folder(on_s3):
    from app.domain import batch_lease

    sources = [{"id": "m-own", "deployment_id": DEP, "file_path": OWN}] + [
        {"id": f"m{n}", "deployment_id": DEP, "file_path": path} for n, path in enumerate(NOT_ITS_OWN.values())
    ]
    items = await batch_lease._items("L1", "annotate", sources)
    assert items[0]["url"] == f"https://s3/originals/{PROJECT}/{DEP}/{HASH}.jpg"
    # Everything else goes through the API, which answers it as a missing original.
    assert [i.get("path") for i in items[1:]] == [f"/api/worker/lease/L1/media/m{n}" for n in range(len(NOT_ITS_OWN))]
    assert on_s3 == [(BUCKET, f"originals/{PROJECT}/{DEP}/{HASH}.jpg")]


async def test_a_lease_whose_folders_cannot_be_read_sends_every_original_through_the_api(on_s3, monkeypatch):
    from app.domain import batch_lease

    monkeypatch.setattr(media_resolver, "_read_projects", MagicMock(side_effect=ConnectionError("down")))
    items = await batch_lease._items("L1", "annotate", [{"id": "m1", "deployment_id": DEP, "file_path": OWN}])
    assert items == [{"media_id": "m1", "deployment_id": DEP, "path": "/api/worker/lease/L1/media/m1"}] and on_s3 == []


async def test_the_lease_media_route_answers_another_folders_original_as_missing(on_s3, monkeypatch):
    from app.domain import batch_lease

    dispatch = {"name": "annotate_deployments_job", "args": [[DEP], "u1"]}
    monkeypatch.setattr(batch_lease, "_lease_rows", lambda lease_id: [{"id": "J1", "status": "processing", "job_data": {"dispatch": dispatch}}])
    row = {
        "id": "m1",
        "deployment_id": DEP,
        "file_path": NOT_ITS_OWN["an export"],
        "media_assets": {"animal_crop_url": NOT_ITS_OWN["a results part"]},
    }
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.execute.return_value = SimpleNamespace(data=[row])
    monkeypatch.setattr(batch_lease, "create_service_client", lambda: client)
    assert await batch_lease.media_served("L1", "m1") is None
    assert await batch_lease.media_served("L1", "m1", crop=True) is None
    row["file_path"] = OWN
    assert (await batch_lease.media_served("L1", "m1")).url.endswith(f"{DEP}/{HASH}.jpg")


async def test_the_export_reads_an_original_only_as_part_of_its_row(folders, reads):
    from app.domain import camtrapdp_export

    assert await camtrapdp_export._fetch_original(OWN, ROW) == b"jpeg"
    assert await camtrapdp_export._fetch_original(OWN, MediaRef("m2", OTHER_DEP)) is None
    assert await camtrapdp_export._fetch_original(NOT_ITS_OWN["another project's original"], ROW) is None
    reads.assert_awaited_once()


async def test_training_reads_each_sample_as_part_of_its_row(monkeypatch):
    from app.domain.training import TrainingSample, resolve_sample_images

    rows_read = []

    async def fake_resolve(path, size="full", media=None):
        rows_read.append(media)
        return b"crop", "image/jpeg"

    monkeypatch.setattr(media_resolver, "resolve_media", fake_resolve)
    samples = [
        TrainingSample("o1", "m1", DEP, "rat", "target", "https://cdn/c1.jpg", False),
        TrainingSample("m2:blank", "m2", None, "background", "background", OWN, False),
    ]
    assert len(await resolve_sample_images(samples)) == 2
    assert rows_read == [MediaRef("m1", DEP), MediaRef("m2", "")]
