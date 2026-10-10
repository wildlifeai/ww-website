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
