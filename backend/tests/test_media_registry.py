# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for Media Registry URL resolution (pure)."""

from io import BytesIO
from unittest.mock import MagicMock

import numpy as np
from PIL import Image

from app.domain import media_registry
from app.domain.media_registry import (
    generate_motion_roi_crops,
    generate_observation_crops,
    group_bursts,
    resolve_url,
    with_resolved_urls,
)


def test_thumbnail_prefers_thumbnail_url():
    row = {"id": "m1", "file_path": "gdrive://abc", "media_assets": {"thumbnail_url": "cdn/t.jpg", "preview_url": "cdn/p.jpg"}}
    assert resolve_url(row, "thumbnail") == "cdn/t.jpg"


def test_thumbnail_falls_back_to_preview_then_original():
    row = {"id": "m1", "file_path": "gdrive://abc", "media_assets": {"preview_url": "cdn/p.jpg"}}
    assert resolve_url(row, "thumbnail") == "cdn/p.jpg"

    row_none = {"id": "m1", "file_path": "gdrive://abc", "media_assets": {}}
    # No renditions, private storage → proxy endpoint (thumb).
    assert resolve_url(row_none, "thumbnail") == "/api/media/m1/image?size=thumb"


def test_preview_falls_back_to_original_not_thumbnail():
    row = {"id": "m1", "file_path": "gdrive://abc", "media_assets": {"thumbnail_url": "cdn/t.jpg"}}
    # preview missing → original (full proxy), never the tiny thumbnail.
    assert resolve_url(row, "preview") == "/api/media/m1/image?size=full"


def test_original_public_url_passthrough():
    row = {"id": "m1", "file_path": "https://example.com/img.jpg", "media_assets": {}}
    assert resolve_url(row, "original") == "https://example.com/img.jpg"


def test_original_private_uses_proxy():
    row = {"id": "m1", "file_path": "gdrive://abc"}
    assert resolve_url(row, "original") == "/api/media/m1/image?size=full"


def test_media_assets_as_list_is_normalised():
    # PostgREST nests a 1:1 relation as a single-element list.
    row = {"id": "m1", "file_path": "gdrive://abc", "media_assets": [{"thumbnail_url": "cdn/t.jpg"}]}
    assert resolve_url(row, "thumbnail") == "cdn/t.jpg"


def test_with_resolved_urls_adds_all_sizes():
    row = {"id": "m1", "file_path": "https://x/y.jpg", "media_assets": [{"thumbnail_url": "cdn/t.jpg"}]}
    out = with_resolved_urls(row)
    assert out["thumbnail_url"] == "cdn/t.jpg"
    assert out["preview_url"] == "https://x/y.jpg"  # no preview rendition → original public url
    assert out["original_url"] == "https://x/y.jpg"
    assert out["id"] == "m1"  # original fields preserved


# ── Burst grouping (pure) ─────────────────────────────────────────────


def _row(mid: str, ts):
    return {"id": mid, "file_path": f"gdrive://{mid}", "timestamp": ts}


def test_group_bursts_splits_on_time_gap():
    rows = [
        _row("a", "2026-06-15T10:00:00Z"),
        _row("b", "2026-06-15T10:00:03Z"),  # +3s → same burst
        _row("c", "2026-06-15T10:05:00Z"),  # +297s → new burst
        _row("d", "2026-06-15T10:05:02Z"),  # +2s → same burst
    ]
    bursts = group_bursts(rows, gap_seconds=10.0)
    assert [[m["id"] for m in b] for b in bursts] == [["a", "b"], ["c", "d"]]


def test_group_bursts_unparseable_timestamp_starts_new_group():
    rows = [_row("a", "2026-06-15T10:00:00Z"), _row("b", None), _row("c", "not-a-date")]
    bursts = group_bursts(rows, gap_seconds=10.0)
    # Each frame stands alone: a has no following neighbour within gap, b/c are unparseable.
    assert [[m["id"] for m in b] for b in bursts] == [["a"], ["b"], ["c"]]


# ── Motion-ROI fallback crop (I/O monkeypatched) ──────────────────────


def _frame_bytes(square=None):
    arr = np.zeros((240, 320, 3), dtype=np.uint8)
    if square is not None:
        x0, y0, x1, y1 = square
        arr[y0:y1, x0:x1, :] = 255
    buf = BytesIO()
    Image.fromarray(arr).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


async def test_generate_motion_roi_crops_writes_for_moving_frames(monkeypatch):
    # A two-frame burst with a moving white square → motion ROI found → crop written.
    rows = [
        _row("a", "2026-06-15T10:00:00Z"),
        _row("b", "2026-06-15T10:00:02Z"),
    ]
    payloads = {
        "gdrive://a": _frame_bytes(square=(120, 90, 160, 130)),
        "gdrive://b": _frame_bytes(square=(150, 95, 190, 135)),
    }

    async def fake_resolve(file_path, size="full"):
        return payloads[file_path], "image/jpeg"

    async def fake_upload(path, data):
        return f"cdn/{path}"

    upserts: list[dict] = []

    async def fake_upsert(patch):
        upserts.append(patch)

    monkeypatch.setattr("app.domain.media_resolver.resolve_media", fake_resolve)
    monkeypatch.setattr("app.services.storage.upload_rendition", fake_upload)
    monkeypatch.setattr(media_registry, "_upsert_media_assets", fake_upsert)

    created = await generate_motion_roi_crops("dep1", rows, burst_gap_seconds=10.0)

    assert created == len(upserts) >= 1
    assert all(u["animal_crop_url"].startswith("cdn/crops/dep1/") for u in upserts)


async def test_generate_motion_roi_crops_skips_existing_crops(monkeypatch):
    rows = [_row("a", "2026-06-15T10:00:00Z"), _row("b", "2026-06-15T10:00:02Z")]
    payloads = {
        "gdrive://a": _frame_bytes(square=(120, 90, 160, 130)),
        "gdrive://b": _frame_bytes(square=(150, 95, 190, 135)),
    }

    async def fake_resolve(file_path, size="full"):
        return payloads[file_path], "image/jpeg"

    async def fake_upload(path, data):
        return f"cdn/{path}"

    upserts: list[dict] = []

    async def fake_upsert(patch):
        upserts.append(patch)

    monkeypatch.setattr("app.domain.media_resolver.resolve_media", fake_resolve)
    monkeypatch.setattr("app.services.storage.upload_rendition", fake_upload)
    monkeypatch.setattr(media_registry, "_upsert_media_assets", fake_upsert)

    # Both frames already have a detection crop → nothing written.
    created = await generate_motion_roi_crops("dep1", rows, skip_media_ids={"a", "b"})
    assert created == 0
    assert upserts == []


def test_generate_motion_roi_crops_singleton_burst_noops():
    # A lone frame can't be differenced; group_bursts yields a singleton the crop loop skips.
    rows = [_row("a", "2026-06-15T10:00:00Z")]
    assert group_bursts(rows, gap_seconds=10.0) == [rows]
    # Exercised indirectly: with <2 frames the function returns 0 without any I/O.
    import asyncio

    assert asyncio.run(generate_motion_roi_crops("dep1", rows)) == 0


def _obs_svc(media_row, obs_rows, crop_updates):
    """Mock service client: media + observations fetch, and crop_url updates."""
    svc = MagicMock()

    def table(name):
        t = MagicMock()
        for m in ("select", "eq", "not_", "order", "limit"):
            getattr(t, m).return_value = t
        t.not_.is_.return_value = t
        t.maybe_single.return_value = t
        if name == "media":
            t.execute.return_value = MagicMock(data=media_row)
        elif name == "observations":
            t.execute.return_value = MagicMock(data=obs_rows)  # the select/fetch path

            def _update(patch):
                u = MagicMock()

                def _eq(_col, obs_id):
                    crop_updates[obs_id] = patch["crop_url"]
                    return MagicMock(execute=lambda: MagicMock(data=[]))

                u.eq.side_effect = _eq
                return u

            t.update.side_effect = _update
        return t

    svc.table.side_effect = table
    return svc


async def test_generate_observation_crops_one_per_box(monkeypatch):
    """Every AI animal detection gets its own crop_url; hero = highest confidence."""
    media_row = {"id": "m1", "deployment_id": "dep1", "file_path": "gdrive://m1"}
    obs_rows = [  # already confidence-desc ordered (mirrors the .order() query)
        {"id": "o-hi", "bbox_x": 0.5, "bbox_y": 0.5, "bbox_w": 0.2, "bbox_h": 0.2, "confidence": 0.9},
        {"id": "o-lo", "bbox_x": 0.1, "bbox_y": 0.1, "bbox_w": 0.2, "bbox_h": 0.2, "confidence": 0.4},
    ]
    frame = _frame_bytes(square=(120, 90, 160, 130))
    crop_updates: dict[str, str] = {}
    uploaded: list[str] = []
    upserts: list[dict] = []

    async def fake_resolve(file_path, size="full"):
        return frame, "image/jpeg"

    async def fake_upload(path, data):
        uploaded.append(path)
        return f"cdn/{path}"

    async def fake_upsert(patch):
        upserts.append(patch)

    monkeypatch.setattr("app.services.supabase_client.create_service_client", lambda: _obs_svc(media_row, obs_rows, crop_updates))
    monkeypatch.setattr("app.domain.media_resolver.resolve_media", fake_resolve)
    monkeypatch.setattr("app.services.storage.upload_rendition", fake_upload)
    monkeypatch.setattr(media_registry, "_upsert_media_assets", fake_upsert)

    hero = await generate_observation_crops("m1")

    # One crop per observation, stored at per-observation paths.
    assert sorted(uploaded) == ["crops/dep1/m1/o-hi.jpg", "crops/dep1/m1/o-lo.jpg"]
    # crop_url written for each observation.
    assert crop_updates == {"o-hi": "cdn/crops/dep1/m1/o-hi.jpg", "o-lo": "cdn/crops/dep1/m1/o-lo.jpg"}
    # Hero (media_assets.animal_crop_url) = highest-confidence crop.
    assert hero == "cdn/crops/dep1/m1/o-hi.jpg"
    assert upserts == [{"media_id": "m1", "animal_crop_url": "cdn/crops/dep1/m1/o-hi.jpg"}]


async def test_generate_observation_crops_no_detections_noops(monkeypatch):
    media_row = {"id": "m1", "deployment_id": "dep1", "file_path": "gdrive://m1"}
    crop_updates: dict[str, str] = {}
    upserts: list[dict] = []

    async def fake_upsert(patch):
        upserts.append(patch)

    monkeypatch.setattr("app.services.supabase_client.create_service_client", lambda: _obs_svc(media_row, [], crop_updates))
    monkeypatch.setattr(media_registry, "_upsert_media_assets", fake_upsert)

    assert await generate_observation_crops("m1") is None
    assert crop_updates == {}
    assert upserts == []


# ── Thumbnail retry and backfill (#208) ───────────────────────────────


class _Refused(Exception):
    """Stands in for postgrest's APIError on a refused write."""

    code = "42501"


def _prep_fakes(monkeypatch, upload_results):
    """Fake I/O for prepare_media_assets; ``upload_results`` feeds thumbnail uploads in order."""
    calls = {"upload": 0}
    upserts: list[dict] = []

    async def fake_resolve(file_path, size="full"):
        return _frame_bytes(), "image/jpeg"

    async def fake_upload(path, data):
        if path.startswith("previews/"):
            return f"cdn/{path}"
        calls["upload"] += 1
        result = upload_results.pop(0) if upload_results else "ok"
        if isinstance(result, Exception):
            raise result
        return f"cdn/{path}" if result == "ok" else None

    async def fake_upsert(patch):
        upserts.append(patch)

    monkeypatch.setattr("app.domain.media_resolver.resolve_media", fake_resolve)
    monkeypatch.setattr("app.services.storage.upload_rendition", fake_upload)
    monkeypatch.setattr(media_registry, "_upsert_media_assets", fake_upsert)
    monkeypatch.setattr(media_registry, "_PREP_BACKOFF_SECONDS", 0)
    return calls, upserts


async def test_prepare_retries_a_failed_upload(monkeypatch):
    calls, upserts = _prep_fakes(monkeypatch, [None, "ok"])

    patch = await media_registry.prepare_media_assets_with_retry({"id": "m1", "deployment_id": "dep1", "file_path": "gdrive://m1"})

    assert calls["upload"] == 2
    assert patch["thumbnail_url"] == "cdn/thumbnails/dep1/m1.jpg"
    assert len(upserts) == 1  # the failed attempt wrote nothing


async def test_prepare_gives_up_after_the_last_attempt(monkeypatch):
    calls, upserts = _prep_fakes(monkeypatch, [None, None, None])

    try:
        await media_registry.prepare_media_assets_with_retry({"id": "m1", "deployment_id": "dep1", "file_path": "gdrive://m1"})
        raise AssertionError("expected RenditionUploadError")
    except media_registry.RenditionUploadError:
        pass
    assert calls["upload"] == media_registry._PREP_ATTEMPTS
    assert upserts == []


async def test_prepare_does_not_retry_a_permission_error(monkeypatch):
    calls, _ = _prep_fakes(monkeypatch, [_Refused("permission denied for table media_assets")])

    try:
        await media_registry.prepare_media_assets_with_retry({"id": "m1", "deployment_id": "dep1", "file_path": "gdrive://m1"})
        raise AssertionError("expected the permission error")
    except _Refused:
        pass
    assert calls["upload"] == 1


def test_is_permission_error():
    assert media_registry.is_permission_error(_Refused("x"))
    assert media_registry.is_permission_error(Exception("permission denied for table media_assets"))
    assert not media_registry.is_permission_error(Exception("timed out"))


def _media_svc(rows):
    """Mock service client whose media query honours .range(start, end)."""
    svc = MagicMock()
    t = MagicMock()
    for m in ("select", "eq", "is_", "order"):
        getattr(t, m).return_value = t

    def _range(start, end):
        return MagicMock(execute=lambda: MagicMock(data=rows[start : end + 1]))

    t.range.side_effect = _range
    svc.table.return_value = t
    return svc


async def test_backfill_pages_and_only_touches_missing_thumbnails(monkeypatch):
    rows = [
        {"id": "a", "deployment_id": "dep1", "file_path": "gdrive://a", "media_assets": {"thumbnail_url": "cdn/a"}},
        {"id": "b", "deployment_id": "dep1", "file_path": "gdrive://b", "media_assets": None},
        {"id": "c", "deployment_id": "dep1", "file_path": "gdrive://c", "media_assets": []},
    ]
    _, upserts = _prep_fakes(monkeypatch, [])
    monkeypatch.setattr("app.services.supabase_client.create_service_client", lambda: _media_svc(rows))
    monkeypatch.setattr(media_registry, "_FETCH_PAGE", 2)  # three rows → two pages
    seen: list[tuple[int, int]] = []

    async def progress(done, total):
        seen.append((done, total))

    generated = await media_registry.backfill_thumbnails("dep1", progress=progress)

    assert generated == 2
    assert sorted(u["media_id"] for u in upserts) == ["b", "c"]
    assert seen[-1] == (2, 2)


async def test_backfill_stops_on_a_permission_error(monkeypatch):
    rows = [{"id": i, "deployment_id": "dep1", "file_path": f"gdrive://{i}", "media_assets": None} for i in ("a", "b", "c")]
    calls, _ = _prep_fakes(monkeypatch, [_Refused("permission denied")])
    monkeypatch.setattr("app.services.supabase_client.create_service_client", lambda: _media_svc(rows))

    try:
        await media_registry.backfill_thumbnails("dep1")
        raise AssertionError("expected the permission error")
    except _Refused:
        pass
    assert calls["upload"] == 1  # stopped at the first photo
