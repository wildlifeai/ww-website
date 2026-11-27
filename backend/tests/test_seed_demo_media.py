# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""scripts/seed_demo_media.py (#461): the demo's photos get their capture time and their labels.

The seeder runs against FakeDB, so every column it writes is checked against ww-backend's
schema, and a second run proves it idempotent. The label file is checked against the fixture
photos it labels.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tests.fake_postgrest import FakeDB

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "test-fixtures" / "camera-trap"

_spec = importlib.util.spec_from_file_location("seed_demo_media", ROOT / "scripts" / "seed_demo_media.py")
assert _spec and _spec.loader
sdm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sdm)

PROJECT = "c0000000-0000-0000-0000-000000000001"
DEPLOYMENT = "e0000000-0000-0000-0000-000000000003"
CAT = "sdcard/dev-sdcard/MEDIA/E005A4AB/IMAGES.000/A2C99A30.JPG"
BLACKBIRD = "sdcard/dev-sdcard/MEDIA/7785FABB/IMAGES.000/A2C72040.JPG"
EMPTY = "sdcard/dev-sdcard/MEDIA/472E4095/IMAGES.000/A2E1DB90.JPG"


class FakeStorage:
    """``svc.storage``: keeps what was uploaded; an object already there raises as Supabase does."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def from_(self, bucket: str):
        storage = self

        class Bucket:
            def upload(self, path, content, file_options=None):
                key = f"{bucket}/{path}"
                if key in storage.objects:
                    raise RuntimeError("The resource already exists")
                storage.objects[key] = content

        return Bucket()


def _db(**tables) -> FakeDB:
    db = FakeDB({"deployments": [{"id": DEPLOYMENT, "project_id": PROJECT, "deleted_at": None}], **tables})
    db.storage = FakeStorage()  # pyright: ignore[reportAttributeAccessIssue]
    return db


def _seed(db: FakeDB, labels: dict[str, dict], paths=(CAT, BLACKBIRD, EMPTY)) -> None:
    sdm.seed(db, "https://dev.supabase.co", PROJECT, [FIXTURES / p for p in paths], "media-renditions", labels)


def _labels() -> dict[str, dict]:
    return sdm.read_labels(FIXTURES / "demo-labels.csv")


def _ours(db: FakeDB) -> dict[str, dict]:
    names = {m["id"]: m["file_name"] for m in db.rows("media")}
    return {names[o["media_id"]]: o for o in db.rows("observations") if o.get("classified_by") == sdm.LABELLED_BY}


def test_the_label_file_labels_every_fixture_photo_once():
    photos = {p.name for p in FIXTURES.rglob("*") if p.is_file() and p.suffix.lower() in sdm.IMAGE_SUFFIXES}
    labels = _labels()
    assert set(labels) == photos
    assert labels["A2C99A30.JPG"] == {"observation_type": "animal", "scientific_name": "Felis catus", "vernacular_name": "Feral cat"}
    assert labels["A2E1DB90.JPG"] == {"observation_type": "blank", "scientific_name": None, "vernacular_name": None}


@pytest.mark.parametrize(
    "rows, error",
    [
        (["a/X.JPG,bird,,"], "unknown observation_type"),
        (["a/X.JPG,blank,Felis catus,"], "only an animal"),
        (["a/X.JPG,blank,,", "b/X.JPG,human,,"], "labelled twice"),
    ],
)
def test_read_labels_refuses_a_file_the_schema_or_the_seeder_cannot_take(tmp_path, rows, error):
    f = tmp_path / "labels.csv"
    f.write_text("\n".join(["path,observation_type,scientific_name,vernacular_name", *rows]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        sdm.read_labels(f)


def test_capture_time_is_the_exif_datetimeoriginal():
    assert sdm.capture_time((FIXTURES / CAT).read_bytes()) == "2026-06-12T23:43:31"
    assert sdm.capture_time(b"\xff\xd8\xff\xd9") is None


def test_a_first_run_registers_dated_photos_with_their_labels():
    db = _db()
    _seed(db, _labels())

    media = {m["file_name"]: m for m in db.rows("media")}
    assert set(media) == {"A2C99A30.JPG", "A2C72040.JPG", "A2E1DB90.JPG"}
    assert media["A2C99A30.JPG"]["timestamp"] == "2026-06-12T23:43:31"
    assert media["A2C99A30.JPG"]["file_path"] == f"https://dev.supabase.co/storage/v1/object/public/media-renditions/demo/{DEPLOYMENT}/A2C99A30.JPG"

    ours = _ours(db)
    assert {n: (o["observation_type"], o.get("scientific_name")) for n, o in ours.items()} == {
        "A2C99A30.JPG": ("animal", "Felis catus"),
        "A2C72040.JPG": ("animal", "Turdus merula"),
        "A2E1DB90.JPG": ("blank", None),
    }
    cat = ours["A2C99A30.JPG"]
    assert cat["deployment_id"] == DEPLOYMENT and cat["observation_level"] == "media"
    assert (cat["source_type"], cat["review_status"], cat.get("classification_method")) == ("imported", "unreviewed", None)
    assert "vernacular_name" not in ours["A2E1DB90.JPG"]  # None is left out, as a CamtrapDP import does


def test_a_second_run_writes_nothing():
    db = _db()
    _seed(db, _labels())
    writes = len(db.writes)
    _seed(db, _labels())
    assert db.writes[writes:] == []
    assert len(db.rows("observations")) == 3


def test_photos_seeded_before_labels_get_dated_and_labelled():
    # Dev today: the 98 media rows exist, with no capture time and no observation.
    db = _db(media=[{"id": "m-cat", "deployment_id": DEPLOYMENT, "file_path": "x", "file_name": "A2C99A30.JPG", "timestamp": None}])
    _seed(db, _labels(), paths=(CAT,))
    assert db.rows("media")[0]["timestamp"] == "2026-06-12T23:43:31"
    assert db.storage.objects == {}  # pyright: ignore[reportAttributeAccessIssue]
    assert _ours(db)["A2C99A30.JPG"]["media_id"] == "m-cat"


def test_a_changed_or_dropped_label_replaces_only_the_seeders_rows():
    db = _db()
    _seed(db, _labels())
    cat_id = next(m["id"] for m in db.rows("media") if m["file_name"] == "A2C99A30.JPG")
    ai = {"id": "ai-1", "deployment_id": DEPLOYMENT, "media_id": cat_id, "observation_type": "animal", "source_type": "ai"}
    db.rows("observations").append(ai)

    labels = _labels()
    labels["A2C99A30.JPG"] = {"observation_type": "animal", "scientific_name": "Felis catus", "vernacular_name": "Cat"}
    del labels["A2E1DB90.JPG"]
    _seed(db, labels)

    ours = _ours(db)
    assert set(ours) == {"A2C99A30.JPG", "A2C72040.JPG"}
    assert ours["A2C99A30.JPG"]["vernacular_name"] == "Cat"
    assert sum(1 for o in db.rows("observations") if o["media_id"] == cat_id) == 2  # the new label and the AI row
    assert any(o["id"] == "ai-1" for o in db.rows("observations"))


def test_a_doubled_label_from_an_interrupted_run_is_made_one():
    db = _db()
    _seed(db, _labels(), paths=(CAT,))
    db.rows("observations").append({**db.rows("observations")[0], "id": "extra"})
    _seed(db, _labels(), paths=(CAT,))
    assert len(_ours(db)) == 1 and len(db.rows("observations")) == 1
