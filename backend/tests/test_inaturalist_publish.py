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

    async def publish_one_burst(_svc, _user_id, _dep, burst, _geoprivacy, _result):
        published.append(([m["id"] for m in burst], pub._best_species(burst)))

    monkeypatch.setattr(pub, "get_user_token", get_user_token)
    monkeypatch.setattr(pub, "create_service_client", lambda: svc)
    monkeypatch.setattr(pub, "_publish_one_burst", publish_one_burst)

    result = await pub.publish_media_to_inat("user-1", ["m1", "m2", "m3", "m4"])

    assert published == [(["m1", "m2"], "Mus musculus")]
    assert result["skipped_bycatch"] == 2
