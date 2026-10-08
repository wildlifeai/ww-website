# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""WW500 test photos are never uploaded (ww-website#287).

A WW500 frame (EXIF Make "Wildlife.ai") with no Deployment_ID tag, or the
all-zero id, was taken before a deployment was set on the camera. The upload
endpoint drops it whatever folder it sits in and counts it; a real tag in
MEDIA/00000000/ still binds, and a file with no EXIF keeps the folder fallback.
"""

import json
import struct
from types import SimpleNamespace

import pytest

from app.domain.exif import ZERO_DEPLOYMENT_ID, is_test_photo, parse_exif_from_bytes
from app.jobs import store
from app.routers import exif as exif_router

REAL = "16bed409-6c1e-4f9b-a3a4-2d1c1e0f9a77"


def _jpeg(make: str | None = None, deployment_id: str | None = None) -> bytes:
    """A minimal little-endian JPEG whose IFD0 holds Make and/or Deployment_ID (0xF200)."""
    entries = []
    if make is not None:
        entries.append((0x010F, make.encode() + b"\x00"))
    if deployment_id is not None:
        entries.append((0xF200, deployment_id.encode() + b"\x00"))
    if not entries:
        return b"\xff\xd8\xff\xd9"
    ifd_len = 2 + 12 * len(entries) + 4
    data_at = 8 + ifd_len
    ifd = struct.pack("<H", len(entries))
    data = b""
    for tag, value in entries:
        ifd += struct.pack("<HHII", tag, 2, len(value), data_at + len(data))
        data += value
    tiff = b"II" + struct.pack("<HI", 42, 8) + ifd + struct.pack("<I", 0) + data
    segment = b"Exif\x00\x00" + tiff
    return b"\xff\xd8\xff\xe1" + struct.pack(">H", len(segment) + 2) + segment + b"\xff\xd9"


class TestIsTestPhoto:
    def test_ww500_frame_with_no_deployment_tag(self):
        assert is_test_photo(parse_exif_from_bytes(_jpeg("Wildlife.ai")))

    def test_ww500_frame_with_the_zero_id(self):
        assert is_test_photo(parse_exif_from_bytes(_jpeg("Wildlife.ai", ZERO_DEPLOYMENT_ID)))

    def test_ww500_frame_with_a_real_tag(self):
        parsed = parse_exif_from_bytes(_jpeg("Wildlife.ai", REAL))
        assert parsed["deployment_id"] == REAL
        assert not is_test_photo(parsed)

    def test_other_camera_without_a_tag(self):
        assert not is_test_photo(parse_exif_from_bytes(_jpeg("Canon")))

    def test_no_exif_at_all(self):
        assert not is_test_photo(parse_exif_from_bytes(_jpeg()))
        assert not is_test_photo(parse_exif_from_bytes(b"BM\x00\x00"))
        assert not is_test_photo({})


@pytest.fixture
def drive_upload(monkeypatch):
    """Drive enabled, a confirmed user, and the enqueue step captured instead of run."""
    captured: dict = {}

    async def user(_authorization):
        return SimpleNamespace(id="user-1", email_confirmed_at="2026-01-01T00:00:00Z")

    async def enqueue(**kwargs):
        captured.update(kwargs)
        return {"enabled": True, "status": "queued", "job_id": "job-1"}

    monkeypatch.setattr(exif_router.settings, "GOOGLE_DRIVE_ENABLED", True)
    monkeypatch.setattr(exif_router, "get_optional_user", user)
    monkeypatch.setattr(exif_router, "_enqueue_drive_upload_with_retry", enqueue)
    return captured


def test_parse_endpoint_drops_test_photos_and_counts_them(client, drive_upload):
    frames = [
        ("MEDIA/00000000/IMAGES.000/A0000010.JPG", _jpeg("Wildlife.ai")),  # no tag
        ("MEDIA/16BED409/IMAGES.000/A0000020.JPG", _jpeg("Wildlife.ai", ZERO_DEPLOYMENT_ID)),  # zero id
        ("MEDIA/00000000/IMAGES.000/A0000030.JPG", _jpeg("Wildlife.ai", REAL)),  # real tag, stale folder
        ("DCIM/IMG_0001.JPG", _jpeg("Canon")),  # another camera
        ("MEDIA/7785FABB/IMAGES.000/A0000040.JPG", _jpeg()),  # no EXIF: folder fallback
    ]
    resp = client.post(
        "/api/exif/parse",
        files=[("files", (path.rsplit("/", 1)[1], body, "image/jpeg")) for path, body in frames],
        data={"paths": [path for path, _ in frames], "upload_to_drive": "true"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["test_photos_skipped"] == 2

    exif = [img["exif"] for img in body["images"]]
    assert [e.get("test_photo", False) for e in exif] == [True, True, False, False, False]
    assert [e.get("deployment_id") for e in exif] == [None, None, REAL, None, "7785FABB"]

    # The zero id never reaches the deployment lookup or auto-create, and the count rides to the job.
    assert drive_upload["deployment_ids"] == [REAL]
    assert drive_upload["folder_prefixes"] == ["7785fabb"]
    assert drive_upload["test_photos_skipped"] == 2


async def test_an_assigned_deployment_never_picks_up_a_test_photo(monkeypatch):
    """The manual-assignment fallback stores id-less frames; a flagged test photo stays out."""
    assigned = "aaaaaaaa-0000-4000-8000-000000000001"
    stored: list = []
    jobs: list = []

    class _Table:
        def __getattr__(self, _name):
            return lambda *a, **k: self

        def execute(self):
            return SimpleNamespace(
                data=[{"id": assigned, "deployment_start": "2026-10-01T00:00:00Z", "location_name": "Bench", "projects": {"id": "p1", "name": "P"}}]
            )

    async def classify(_user_id, ids):
        return {i: "valid" for i in ids}

    async def store_blob(blob_id, content, metadata):
        stored.append(content)

    async def create_job(**kwargs):
        return "job-1"

    monkeypatch.setattr(exif_router, "create_service_client", lambda: SimpleNamespace(table=lambda _n: _Table()))
    monkeypatch.setattr(exif_router, "classify_deployment_access", classify)
    monkeypatch.setattr(exif_router, "store_blob", store_blob)
    monkeypatch.setattr(exif_router, "create_job", create_job)
    monkeypatch.setattr(exif_router, "upload_drive_images_job", lambda job_id, payload: jobs.append(payload))
    monkeypatch.setattr(exif_router, "enqueue_local_job", lambda _coro: None)

    files = [SimpleNamespace(filename="test.jpg"), SimpleNamespace(filename="loose.jpg")]
    out = await exif_router._enqueue_drive_upload(
        request=None,
        files=files,
        file_contents=[b"test", b"loose"],
        results=[
            {"filename": "test.jpg", "exif": {"deployment_id": None, "test_photo": True}},
            {"filename": "loose.jpg", "exif": {"deployment_id": None}},
        ],
        deployment_ids=[],
        user_id="user-1",
        assigned_deployment_id=assigned,
        test_photos_skipped=1,
    )
    assert out["file_count"] == 1
    assert stored == [b"loose"]
    assert jobs[0]["test_photos_skipped"] == 1


async def test_job_summary_records_test_photos_skipped(monkeypatch):
    async def no_sync(*_a, **_k):
        return None

    monkeypatch.setattr(store, "_sync_to_supabase", no_sync)
    store._memory_store["job:j1"] = json.dumps({"job_id": "j1", "summary": None})
    try:
        await store.update_summary("j1", total=3, test_photos_skipped=2)
        summary = json.loads(store._memory_store["job:j1"])["summary"]
        assert summary["total"] == 3
        assert summary["test_photos_skipped"] == 2
    finally:
        store._memory_store.pop("job:j1", None)
