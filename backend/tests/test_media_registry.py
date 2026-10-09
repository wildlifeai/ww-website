# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for Media Registry URL resolution, the routes that call it, and rendition generation."""

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


def test_thumbnail_falls_back_to_preview_then_public_original():
    row = {"id": "m1", "file_path": "gdrive://abc", "media_assets": {"preview_url": "cdn/p.jpg"}}
    assert resolve_url(row, "thumbnail") == "cdn/p.jpg"

    row_public = {"id": "m1", "file_path": "https://example.com/img.jpg", "media_assets": {}}
    assert resolve_url(row_public, "thumbnail") == "https://example.com/img.jpg"


def test_private_original_without_rendition_resolves_to_none():
    # #305: the auth-gated /api/media/{id}/image proxy cannot load in a plain <img>,
    # so a private original with no rendition gets no URL at all, at every size.
    for assets in ({}, [], None, {"animal_crop_url": "cdn/c.jpg"}):
        row = {"id": "m1", "file_path": "gdrive://abc", "media_assets": assets}
        for size in ("thumbnail", "preview", "original"):
            assert resolve_url(row, size) is None, (assets, size)


def test_missing_file_path_resolves_to_none():
    row = {"id": "m1", "file_path": None, "media_assets": {}}
    assert resolve_url(row, "thumbnail") is None
    assert resolve_url(row, "original") is None


def test_preview_falls_back_to_original_not_thumbnail():
    row = {"id": "m1", "file_path": "https://example.com/img.jpg", "media_assets": {"thumbnail_url": "cdn/t.jpg"}}
    # preview missing → public original, never the tiny thumbnail.
    assert resolve_url(row, "preview") == "https://example.com/img.jpg"

    private = {"id": "m1", "file_path": "gdrive://abc", "media_assets": {"thumbnail_url": "cdn/t.jpg"}}
    assert resolve_url(private, "preview") is None


def test_original_public_url_passthrough():
    row = {"id": "m1", "file_path": "https://example.com/img.jpg", "media_assets": {}}
    assert resolve_url(row, "original") == "https://example.com/img.jpg"


def test_original_private_is_none_even_with_renditions():
    row = {"id": "m1", "file_path": "gdrive://abc", "media_assets": {"thumbnail_url": "cdn/t.jpg", "preview_url": "cdn/p.jpg"}}
    assert resolve_url(row, "original") is None


def test_no_size_hands_out_the_image_proxy():
    rows = [
        {"id": "m1", "file_path": "gdrive://abc", "media_assets": {}},
        {"id": "m2", "file_path": "drive/folder/x.jpg"},
        {"id": "m3", "file_path": "", "media_assets": [{"thumbnail_url": "cdn/t.jpg"}]},
    ]
    for row in rows:
        for size in ("thumbnail", "preview", "original"):
            assert "/api/media/" not in (resolve_url(row, size) or "")


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


def test_with_resolved_urls_private_without_rendition_is_all_none():
    row = {"id": "m1", "file_path": "gdrive://abc", "file_name": "a.jpg", "media_assets": []}
    out = with_resolved_urls(row)
    assert out["thumbnail_url"] is None
    assert out["preview_url"] is None
    assert out["original_url"] is None
    assert out["file_name"] == "a.jpg"


def test_with_resolved_urls_private_keeps_renditions():
    row = {"id": "m1", "file_path": "gdrive://abc", "media_assets": {"thumbnail_url": "cdn/t.jpg", "preview_url": "cdn/p.jpg"}}
    out = with_resolved_urls(row)
    assert (out["thumbnail_url"], out["preview_url"], out["original_url"]) == ("cdn/t.jpg", "cdn/p.jpg", None)


# ── Registry routes (the callers) ────────────────────────────────────


def _route_client(monkeypatch, data):
    """A TestClient with auth bypassed, the flag on and the anon client returning ``data``."""
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from app.authz import require_deployment_access, require_media_access
    from app.config import settings
    from app.dependencies import get_current_user
    from app.main import app
    from app.routers import media as media_router

    table = MagicMock()
    for name in ("select", "eq", "is_", "order", "range", "maybe_single"):
        getattr(table, name).return_value = table
    table.execute.return_value = SimpleNamespace(data=data)
    anon = MagicMock()
    anon.table.return_value = table
    monkeypatch.setattr(media_router.supabase_client, "create_anon_client", lambda: anon)
    monkeypatch.setattr(settings, "FF_MEDIA_REGISTRY_ENABLED", True)

    overrides = {
        get_current_user: lambda: SimpleNamespace(id="u1", email="t@ww.ai"),
        require_media_access: lambda: None,
        require_deployment_access: lambda: None,
    }
    app.dependency_overrides.update(overrides)
    return TestClient(app), overrides


def _clear(overrides):
    from app.main import app

    for dep in overrides:
        app.dependency_overrides.pop(dep, None)


def test_registry_route_returns_null_urls_for_private_original_without_rendition(monkeypatch):
    rows = [
        {"id": "m1", "file_path": "gdrive://abc", "file_name": "a.jpg", "timestamp": None, "deployment_id": "d1", "media_assets": []},
        {
            "id": "m2",
            "file_path": "gdrive://def",
            "file_name": "b.jpg",
            "timestamp": None,
            "deployment_id": "d1",
            "media_assets": [{"thumbnail_url": "cdn/t.jpg", "preview_url": "cdn/p.jpg"}],
        },
    ]
    client, overrides = _route_client(monkeypatch, rows)
    try:
        body = client.get("/api/media/registry/d1").json()
    finally:
        _clear(overrides)
    m1, m2 = body["data"]["media"]
    assert (m1["thumbnail_url"], m1["preview_url"], m1["original_url"]) == (None, None, None)
    assert (m2["thumbnail_url"], m2["preview_url"], m2["original_url"]) == ("cdn/t.jpg", "cdn/p.jpg", None)
    assert body["data"]["count"] == 2


def test_resolve_route_returns_null_url_for_private_original_without_rendition(monkeypatch):
    client, overrides = _route_client(monkeypatch, {"id": "m1", "file_path": "gdrive://abc", "media_assets": []})
    try:
        body = client.get("/api/media/m1/resolve?size=preview").json()
    finally:
        _clear(overrides)
    assert body["error"] is None
    assert body["data"] == {"media_id": "m1", "size": "preview", "url": None}


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
