# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Crop files and fingerprints for boxes people draw (#449): a person's box gets its crop file at
once and loses its stale fingerprint, a removed box loses both, and the Wildlife Brain takes a
person's box crop over the AI's hero crop, with label checks on."""

import io
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app import authz
from app.config import settings
from app.dependencies import get_current_user
from app.domain import wildlife_brain
from app.domain.media_registry import BoxCropRefused, RenditionUploadError, write_box_crop
from app.routers import media as media_router
from tests.fake_postgrest import FakeDB


def _jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (100, 80), (40, 120, 40)).save(out, format="JPEG")
    return out.getvalue()


def _human(oid="h1", media_id="m1", box=True):
    row = {
        "id": oid,
        "deployment_id": "d1",
        "media_id": media_id,
        "observation_type": "animal",
        "scientific_name": "Rattus rattus",
        "source_type": "human",
        "classification_method": "human",
        "review_status": "human_reviewed",
        "crop_url": None,
        "created_at": "2026-11-27T00:00:00+00:00",
    }
    return {**row, **({"bbox_x": 0.1, "bbox_y": 0.2, "bbox_w": 0.3, "bbox_h": 0.4} if box else {})}


def _db(observations=(), roles=()):
    return FakeDB(
        {
            "projects": [{"id": "p1", "organisation_id": "org-1"}],
            "deployments": [{"id": "d1", "project_id": "p1"}],
            "media": [{"id": m, "deployment_id": "d1", "file_path": f"gdrive://{m}"} for m in ("m1", "m2", "m3")],
            "media_assets": [
                {"media_id": "m1", "animal_crop_url": "https://x/ai-m1.jpg"},
                {"media_id": "m2", "animal_crop_url": "https://x/ai-m2.jpg"},
            ],
            "observations": [dict(o) for o in observations],
            "media_embeddings": [{"media_id": "m1", "deployment_id": "d1", "embedding": "[1,0]", "embedding_model": "dinov3-vith"}],
            "user_roles": [{"user_id": "u1", "is_active": True, **r} for r in roles],
        }
    )


@pytest.fixture
def wired(monkeypatch):
    """The storage, the photo and the vector store, faked; returns what was uploaded."""
    uploads: list[str] = []

    def use(db, photo: bytes | None = None):
        async def resolve(file_path, size="full", media=None):
            return None if photo is None else (photo, "image/jpeg")

        async def upload(path, content, content_type="image/jpeg", bucket=None):
            uploads.append(path)
            return f"https://x/{path}"

        monkeypatch.setattr("app.services.supabase_client.create_service_client", db.client)
        monkeypatch.setattr("app.services.vector_store.create_service_client", db.client)
        monkeypatch.setattr("app.domain.media_resolver.resolve_media", resolve)
        monkeypatch.setattr("app.services.storage.upload_rendition", upload)
        return uploads

    return use


async def test_a_drawn_box_gets_its_crop_and_loses_its_fingerprint(wired):
    db = _db([_human()])
    uploads = wired(db, _jpeg())
    url = await write_box_crop("m1", "h1")
    assert url == "https://x/crops/d1/m1/h1.jpg" and uploads == ["crops/d1/m1/h1.jpg"]
    assert db.tables["observations"][0]["crop_url"] == url
    assert db.tables["media_embeddings"][0]["embedding"] is None and db.tables["media_embeddings"][0]["embedding_model"] is None


async def test_a_removed_box_clears_its_crop(wired):
    db = _db([{**_human(box=False), "crop_url": "https://x/old.jpg"}])
    uploads = wired(db, _jpeg())
    assert await write_box_crop("m1", "h1") is None
    assert db.tables["observations"][0]["crop_url"] is None and uploads == []
    assert db.tables["media_embeddings"][0]["embedding"] is None


AI_ROW = {**_human(), "source_type": "ai", "classification_method": "machine"}


@pytest.mark.parametrize("row, media_id", [(AI_ROW, "m1"), (_human(), "m2"), ({**_human(), "deleted_at": "2026-11-27"}, "m1")])
async def test_only_a_live_persons_row_of_the_photo_is_cropped(wired, row, media_id):
    db = _db([row])
    wired(db, _jpeg())
    with pytest.raises(BoxCropRefused):
        await write_box_crop(media_id, "h1")
    assert db.tables["media_embeddings"][0]["embedding"] == "[1,0]"


async def test_an_unreadable_photo_fails_the_crop_and_keeps_the_fingerprint(wired):
    db = _db([_human()])
    wired(db, None)
    with pytest.raises(RenditionUploadError):
        await write_box_crop("m1", "h1")
    assert db.tables["media_embeddings"][0]["embedding"] == "[1,0]"


# ── The Wildlife Brain's crops ───────────────────────────────────────


def test_a_persons_box_crop_wins_and_a_photo_only_a_person_boxed_joins():
    rows = [{"id": "m1", "deployment_id": "d1", "crop_url": "ai-m1"}, {"id": "m2", "deployment_id": "d1", "crop_url": "ai-m2"}]
    boxes = {"m1": ("d1", "box-m1"), "m3": ("d1", "box-m3")}
    assert wildlife_brain.with_person_boxes(rows, boxes) == [
        {"id": "m1", "deployment_id": "d1", "crop_url": "box-m1"},
        {"id": "m2", "deployment_id": "d1", "crop_url": "ai-m2"},
        {"id": "m3", "deployment_id": "d1", "crop_url": "box-m3"},
    ]


WITH_BOXES = {"m1": "https://x/box-m1.jpg", "m2": "https://x/ai-m2.jpg", "m3": "https://x/box-m3.jpg"}


@pytest.mark.parametrize("on, expected", [(True, WITH_BOXES), (False, None)])
async def test_the_brain_fetches_box_crops_with_label_checks_on(monkeypatch, on, expected):
    old = {**_human("h0", "m1"), "crop_url": "https://x/old-m1.jpg", "created_at": "2026-11-01T00:00:00+00:00"}
    observations = [old, {**_human("h1", "m1"), "crop_url": "https://x/box-m1.jpg"}, {**_human("h3", "m3"), "crop_url": "https://x/box-m3.jpg"}]
    observations.append({**_human("h2", "m2", box=False), "crop_url": "https://x/whole-m2.jpg"})  # no box: not a box crop
    db = _db(observations)
    monkeypatch.setattr("app.services.supabase_client.create_service_client", db.client)
    monkeypatch.setattr(settings, "FF_LABEL_CHECKS_ENABLED", on)
    crops = {r["id"]: r["crop_url"] for r in await wildlife_brain._fetch_crops("d1")}
    assert crops == (expected or {"m1": "https://x/ai-m1.jpg", "m2": "https://x/ai-m2.jpg"})


# ── Route ────────────────────────────────────────────────────────────

MEMBER = {"role": "project_member", "scope_type": "project", "scope_id": "p1"}
VIEWER = {"role": "project_viewer", "scope_type": "project", "scope_id": "p1"}


@pytest.fixture
def api(monkeypatch, wired):
    def make(db, roles, photo=b""):
        db.tables["user_roles"] = [{"user_id": "u1", "is_active": True, **r} for r in roles]
        wired(db, photo or _jpeg())
        monkeypatch.setattr(authz, "create_service_client", db.client)
        monkeypatch.setattr(settings, "FF_LABEL_CHECKS_ENABLED", True)
        app = FastAPI()
        app.include_router(media_router.router)
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u1", email="u@ww.ai", app_metadata={})
        return TestClient(app)

    return make


def test_the_route_crops_a_members_box(api):
    r = api(_db([_human()]), [MEMBER]).post("/api/media/m1/observations/h1/crop")
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"observation_id": "h1", "crop_url": "https://x/crops/d1/m1/h1.jpg"}


def test_the_route_refuses_a_viewer_and_an_ai_row(api):
    assert api(_db([_human()]), [VIEWER]).post("/api/media/m1/observations/h1/crop").status_code == 403
    ai = {**_human(), "source_type": "ai", "classification_method": "machine"}
    assert api(_db([ai]), [MEMBER]).post("/api/media/m1/observations/h1/crop").json()["error"]["code"] == "NOT_FOUND"


def test_the_route_answers_feature_disabled_with_label_checks_off(api, monkeypatch):
    client = api(_db([_human()]), [MEMBER])
    monkeypatch.setattr(settings, "FF_LABEL_CHECKS_ENABLED", False)
    assert client.post("/api/media/m1/observations/h1/crop").json()["error"]["code"] == "FEATURE_DISABLED"
