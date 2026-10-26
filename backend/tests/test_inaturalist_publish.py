# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for publishing bursts to iNaturalist (app/domain/inaturalist_publish.py)."""

from unittest.mock import MagicMock

from app.domain import inaturalist_publish as pub


def _media(mid: str, ts: str | None) -> dict:
    return {"id": mid, "timestamp": ts, "file_path": f"{mid}.jpg"}


def test_cluster_bursts_orders_by_time_and_keeps_untimed_alone():
    media = [
        _media("late", "2026-04-12T10:10:00Z"),
        _media("none", None),
        _media("first", "2026-04-12T10:00:00Z"),
        _media("second", "2026-04-12T10:00:30Z"),
        _media("bad", "not a time"),
    ]
    bursts = pub._cluster_bursts(media, gap_seconds=120)
    assert [[m["id"] for m in b] for b in bursts] == [["first", "second"], ["late"], ["none"], ["bad"]]


async def test_publish_one_burst_without_inat_id_records_nothing(monkeypatch):
    """iNat answering without an observation id must not leave a local row with no id
    or try to attach photos to observation "None"."""

    async def create_observation(**_kwargs):
        return {"uuid": "u-1"}

    async def upload_observation_photo(**_kwargs):
        raise AssertionError("no photo upload without an observation id")

    monkeypatch.setattr(pub, "create_observation", create_observation)
    monkeypatch.setattr(pub, "upload_observation_photo", upload_observation_photo)
    svc = MagicMock()
    result = {"observations_created": 0, "photos_uploaded": 0, "errors": 0, "observations": []}
    dep = {"id": "dep-1", "latitude": -36.8, "longitude": 174.7, "name": "Site"}

    burst = [{**_media("m1", "2026-04-12T10:00:00Z"), "_label": {}}]
    await pub._publish_one_burst(svc, "user-1", dep, burst, "obscured", result)

    assert result["errors"] == 1
    assert result["observations_created"] == 0
    assert result["observations"] == []
    svc.table.assert_not_called()


def _obs(mid: str, kind: str, name: str | None, source: str, at: str) -> dict:
    status = "human_reviewed" if source == "human" else "ai_reviewed"
    return {
        "id": f"{mid}-{source}-{kind}-{at}",
        "media_id": mid,
        "observation_type": kind,
        "scientific_name": name,
        "source_type": source,
        "review_status": status,
        "created_at": at,
    }


T0, T1 = "2026-04-12T10:00:00+00:00", "2026-04-13T10:00:00+00:00"


def test_photo_label_is_the_label_the_card_shows():
    """One rule with the grid and the public API (public_api.photo_verdict): a person's label
    wins over the AI's, and a blank consensus with no person's label is Empty."""
    ai = _obs("m1", "animal", "Felis catus", "ai", T0)
    reviewed = _obs("m1", "animal", "Mus musculus", "human", T1)
    assert pub._photo_label([ai, reviewed]) == reviewed
    assert pub._photo_label([ai]) == ai
    assert pub._photo_label([ai, _obs("m1", "blank", None, "consensus", T1)]) is None


def test_photo_label_keeps_blank_and_people_off():
    ai = _obs("m1", "animal", "Rattus rattus", "ai", T0)
    assert pub._photo_label([ai, _obs("m1", "blank", None, "human", T1)]) is None
    assert pub._photo_label([ai, _obs("m1", "human", None, "human", T1)]) is None
    # A model saw a person: the photo stays off even though a reviewer named the animal.
    person = _obs("m1", "human", "Homo sapiens", "ai", T0)
    assert pub._photo_label([person, _obs("m1", "animal", "Rattus rattus", "human", T1)]) is None
    assert pub._photo_label([]) is None


class _Query:
    """Just enough of a supabase query for publish_media_to_inat's reads."""

    def __init__(self, rows: list):
        self._rows = rows

    def select(self, *_args):
        return self

    def in_(self, *_args):
        return self

    def is_(self, *_args):
        return self

    def order(self, *_args):
        return self

    def range(self, *_args):
        return self

    def execute(self):
        return MagicMock(data=self._rows)


async def test_publish_uses_each_photos_current_label(monkeypatch):
    """A photo goes up under the label its card shows: the reviewer's species over the AI's. A
    photo the reviewer marked blank, and one where a model saw a person, are by-catch."""
    tables = {
        "media": [
            {"id": "m1", "deployment_id": "dep-1", "file_path": "m1.jpg", "timestamp": "2026-04-12T09:00:00Z"},
            {"id": "m2", "deployment_id": "dep-1", "file_path": "m2.jpg", "timestamp": "2026-04-12T09:00:10Z"},
            {"id": "m3", "deployment_id": "dep-1", "file_path": "m3.jpg", "timestamp": "2026-04-12T09:00:20Z"},
            {"id": "m4", "deployment_id": "dep-1", "file_path": "m4.jpg", "timestamp": "2026-04-12T09:00:30Z"},
        ],
        "observations": [
            _obs("m1", "animal", "Felis catus", "ai", T0),
            _obs("m1", "animal", "Mus musculus", "human", T1),
            _obs("m2", "animal", "Felis catus", "ai", T0),
            _obs("m2", "animal", "Mus musculus", "human", T1),
            _obs("m3", "animal", "Rattus rattus", "ai", T0),
            _obs("m3", "blank", None, "human", T1),
            _obs("m4", "animal", "Rattus rattus", "ai", T0),
            _obs("m4", "human", "Homo sapiens", "ai", T0),
        ],
        "deployments": [{"id": "dep-1", "name": "Site", "latitude": -36.8, "longitude": 174.7}],
        "inat_observation_media": [],
    }
    svc = MagicMock()
    svc.table.side_effect = lambda name: _Query(tables[name])

    async def get_user_token(_user_id):
        return {"api_token": "jwt"}

    published: list[tuple[list[str], str | None]] = []

    async def publish_one_burst(_svc, _user_id, _dep, burst, _geoprivacy, _result, _time_up):
        published.append(([m["id"] for m in burst], pub._best_species(burst)))
        return []

    monkeypatch.setattr(pub, "get_user_token", get_user_token)
    monkeypatch.setattr(pub, "create_service_client", lambda: svc)
    monkeypatch.setattr(pub, "_publish_one_burst", publish_one_burst)

    result = await pub.publish_media_to_inat("user-1", ["m1", "m2", "m3", "m4"])

    assert published == [(["m1", "m2"], "Mus musculus")]
    assert result["skipped_bycatch"] == 2


class _Db:
    """The tables a publish reads and writes, filtered on ``in_`` like PostgREST."""

    def __init__(self, media: list, observations: list, inat_rows: list | None = None, links: list | None = None):
        self.rows = {
            "media": media,
            "observations": observations,
            "deployments": [{"id": "dep-1", "name": "Site", "latitude": -36.8, "longitude": 174.7}],
        }
        self.inat_rows = inat_rows or []
        self.links = links or []

    def table(self, name: str) -> "_Table":
        return _Table(self, name)


class _Table:
    def __init__(self, db: _Db, name: str):
        self.db, self.name = db, name
        self.where: tuple | None = None
        self.written: tuple | None = None

    def select(self, *_args):
        return self

    def in_(self, column, values):
        self.where = (column, set(values))
        return self

    def is_(self, *_args):
        return self

    def order(self, *_args):
        return self

    def range(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def insert(self, row):
        self.written = ("insert", row)
        return self

    def update(self, patch):
        self.written = ("update", patch)
        return self

    def execute(self):
        if self.written and self.written[0] == "insert":
            row = dict(self.written[1])
            if self.name == "inat_observations":
                row["id"] = f"row-{len(self.db.inat_rows) + 1}"
                self.db.inat_rows.append(row)
            else:
                self.db.links.append(row)
            return MagicMock(data=[row])
        if self.written:
            return MagicMock(data=[])
        if self.name == "inat_observation_media":
            by_id = {r["id"]: r for r in self.db.inat_rows}
            rows = [{**link, "inat_observations": by_id[link["inat_observation_id"]]} for link in self.db.links]
        else:
            rows = self.db.rows[self.name]
        if self.where:
            column, values = self.where
            rows = [r for r in rows if r[column] in values]
        return MagicMock(data=[dict(r) for r in rows])  # fresh rows each read, as over HTTP


def _photo(mid: str, ts: str) -> dict:
    return {"id": mid, "deployment_id": "dep-1", "file_path": f"{mid}.jpg", "file_name": mid, "timestamp": ts}


def _fake_inat(monkeypatch, db: _Db, seconds_per_photo: float) -> dict:
    """iNaturalist, the photo store and a clock that moves on with each photo upload."""
    calls: dict = {"created": [], "uploaded": [], "now": 0.0}

    async def get_user_token(_user_id):
        return {"api_token": "jwt"}

    async def create_observation(**kwargs):
        calls["created"].append(kwargs["species_guess"])
        return {"id": 100 + len(calls["created"]), "uuid": None}

    async def resolve_media(file_path, size="full"):
        return b"jpeg", "image/jpeg"

    async def upload_observation_photo(**kwargs):
        calls["uploaded"].append((kwargs["observation_id"], kwargs["filename"]))
        calls["now"] += seconds_per_photo
        return {"id": len(calls["uploaded"])}

    monkeypatch.setattr(pub, "get_user_token", get_user_token)
    monkeypatch.setattr(pub, "create_service_client", lambda: db)
    monkeypatch.setattr(pub, "create_observation", create_observation)
    monkeypatch.setattr(pub, "resolve_media", resolve_media)
    monkeypatch.setattr(pub, "upload_observation_photo", upload_observation_photo)
    return calls


async def test_a_long_publish_hands_back_the_rest_and_carries_on_in_the_same_observation(monkeypatch):
    """A publish stops starting photos after its budget, inside the ingress timeout, and hands back
    what it did not reach. Sent again, the rest of a burst joins the observation it started (#123)."""
    media = [
        _photo("a1", "2026-04-12T10:00:00Z"),
        _photo("a2", "2026-04-12T10:00:10Z"),
        _photo("a3", "2026-04-12T10:00:20Z"),
        _photo("b1", "2026-04-12T11:00:00Z"),
    ]
    db = _Db(media, [_obs(m["id"], "animal", "Rattus rattus", "ai", T0) for m in media])
    calls = _fake_inat(monkeypatch, db, seconds_per_photo=70)

    def clock():
        return calls["now"]

    first = await pub.publish_media_to_inat("u1", ["a1", "a2", "a3", "b1"], budget_seconds=120, clock=clock)

    assert (first["observations_created"], first["photos_uploaded"]) == (1, 2)
    assert sorted(first["remaining_media_ids"]) == ["a1", "a2", "a3", "b1"]

    second = await pub.publish_media_to_inat("u1", first["remaining_media_ids"], budget_seconds=120, clock=clock)

    assert (second["observations_created"], second["photos_uploaded"]) == (1, 2)
    assert second["skipped_already_published"] == 2
    assert second["remaining_media_ids"] == []
    assert calls["created"] == ["Rattus rattus", "Rattus rattus"]
    assert calls["uploaded"] == [(101, "a1"), (101, "a2"), (101, "a3"), (102, "b1")]
    assert [link["inat_observation_id"] for link in db.links] == ["row-1", "row-1", "row-1", "row-2"]


async def test_a_publish_within_its_budget_hands_nothing_back(monkeypatch):
    media = [_photo("a1", "2026-04-12T10:00:00Z"), _photo("b1", "2026-04-12T11:00:00Z")]
    db = _Db(media, [_obs(m["id"], "animal", "Rattus rattus", "ai", T0) for m in media])
    calls = _fake_inat(monkeypatch, db, seconds_per_photo=1)

    result = await pub.publish_media_to_inat("u1", ["a1", "b1"], clock=lambda: calls["now"])

    assert (result["observations_created"], result["photos_uploaded"], result["remaining_media_ids"]) == (2, 2, [])


async def test_only_the_publishers_own_observation_takes_a_burst_mate(monkeypatch):
    """A burst-mate of a photo someone else published goes into an observation of the caller's own:
    the other person's iNaturalist observation is theirs."""
    media = [_photo("a1", "2026-04-12T10:00:00Z"), _photo("a2", "2026-04-12T10:00:10Z")]
    theirs = {"id": "row-1", "user_id": "u2", "inat_observation_id": 900, "inat_uri": None, "species_guess": None}
    db = _Db(
        media,
        [_obs(m["id"], "animal", "Rattus rattus", "ai", T0) for m in media],
        inat_rows=[theirs],
        links=[{"inat_observation_id": "row-1", "media_id": "a1"}],
    )
    calls = _fake_inat(monkeypatch, db, seconds_per_photo=1)

    result = await pub.publish_media_to_inat("u1", ["a1", "a2"], clock=lambda: calls["now"])

    assert result["skipped_already_published"] == 1
    assert calls["uploaded"] == [(101, "a2")]


async def test_a_failed_photo_is_not_handed_back(monkeypatch):
    media = [_photo("a1", "2026-04-12T10:00:00Z"), _photo("a2", "2026-04-12T10:00:10Z"), _photo("a3", "2026-04-12T10:00:20Z")]
    db = _Db(media, [_obs(m["id"], "animal", "Rattus rattus", "ai", T0) for m in media])
    calls = _fake_inat(monkeypatch, db, seconds_per_photo=1)

    async def missing_first(file_path, size="full"):
        return None if file_path == "a1.jpg" else (b"jpeg", "image/jpeg")

    monkeypatch.setattr(pub, "resolve_media", missing_first)

    result = await pub.publish_media_to_inat("u1", ["a1", "a2", "a3"], budget_seconds=0, clock=lambda: calls["now"])

    assert result["remaining_media_ids"] == ["a2", "a3"]
    assert result["observations"][0]["photo_errors"] == 1
