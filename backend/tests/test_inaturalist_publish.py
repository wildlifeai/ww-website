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

    burst = [{**_media("m1", "2026-04-12T10:00:00Z"), "_animal_obs": []}]
    await pub._publish_one_burst(svc, "user-1", dep, burst, "obscured", result)

    assert result["errors"] == 1
    assert result["observations_created"] == 0
    assert result["observations"] == []
    svc.table.assert_not_called()


def _obs(mid: str, kind: str, name: str | None, source: str, at: str) -> dict:
    status = "human_reviewed" if source == "human" else "ai_reviewed"
    return {
        "media_id": mid,
        "observation_type": kind,
        "scientific_name": name,
        "source_type": source,
        "review_status": status,
        "created_at": at,
        "updated_at": at,
    }


def test_photo_labels_take_the_newest_review_over_older_ones_and_the_ai():
    ai = _obs("m1", "animal", "Rattus rattus", "ai", "2026-04-12T10:00:00+00:00")
    confirmed = _obs("m1", "animal", "Felis catus", "human", "2026-04-13T10:00:00+00:00")
    reassigned = _obs("m1", "animal", "Mus musculus", "human", "2026-04-14T10:00:00+00:00")
    assert pub._photo_labels([ai, reassigned, confirmed]) == [reassigned]
    assert pub._photo_labels([ai]) == [ai]


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

    def execute(self):
        return MagicMock(data=self._rows)


async def test_publish_uses_each_photos_current_label(monkeypatch):
    """A cluster confirmed as one species and then reassigned goes up as the new species, and a
    photo the reviewer marked blank is by-catch even though the AI called it an animal."""
    t0, t1, t2 = "2026-04-12T10:00:00+00:00", "2026-04-13T10:00:00+00:00", "2026-04-14T10:00:00+00:00"
    tables = {
        "media": [
            {"id": "m1", "deployment_id": "dep-1", "file_path": "m1.jpg", "timestamp": "2026-04-12T09:00:00Z"},
            {"id": "m2", "deployment_id": "dep-1", "file_path": "m2.jpg", "timestamp": "2026-04-12T09:00:10Z"},
            {"id": "m3", "deployment_id": "dep-1", "file_path": "m3.jpg", "timestamp": "2026-04-12T09:00:20Z"},
        ],
        "observations": [
            _obs("m1", "animal", "Felis catus", "human", t1),
            _obs("m1", "animal", "Mus musculus", "human", t2),
            _obs("m2", "animal", "Felis catus", "human", t1),
            _obs("m2", "animal", "Mus musculus", "human", t2),
            _obs("m3", "animal", "Rattus rattus", "ai", t0),
            _obs("m3", "blank", None, "human", t1),
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

    result = await pub.publish_media_to_inat("user-1", ["m1", "m2", "m3"])

    assert published == [(["m1", "m2"], "Mus musculus")]
    assert result["skipped_bycatch"] == 1
