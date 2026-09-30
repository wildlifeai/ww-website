# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""services/media_evidence.py: the one-shot table probe, row shape, the upsert and the read-back (client mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services import media_evidence as me


@pytest.fixture(autouse=True)
def _fresh_probe():
    me.reset_probe()
    yield
    me.reset_probe()


class _Missing(Exception):
    code = "PGRST205"


def _svc(probe_error: Exception | None = None, rows: list[dict] | None = None):
    calls = {"probe": 0, "upserts": [], "reads": 0}
    table = MagicMock()

    def _select(cols="id", *a, **k):
        q = MagicMock()
        q.limit.return_value = q
        q.in_.return_value = q
        q.eq.return_value = q

        def _exec():
            if cols == "id":
                calls["probe"] += 1
                if probe_error is not None:
                    raise probe_error
                return MagicMock(data=[])
            calls["reads"] += 1
            return MagicMock(data=rows or [])

        q.execute.side_effect = _exec
        return q

    table.select.side_effect = _select

    def _upsert(rows, on_conflict=""):
        calls["upserts"].append((list(rows), on_conflict))
        q = MagicMock()
        q.execute.return_value = MagicMock(data=rows)
        return q

    table.upsert.side_effect = _upsert
    svc = MagicMock()
    svc.table.return_value = table
    return svc, calls


def test_signal_rows_shape_and_value_routing():
    rows = me.signal_rows(
        "m1",
        "d1",
        {
            "speciesnet_presence": 1.0,
            "gemini_visibility": (0.66, "partial"),
            "burst_id": "dep:m1",
            "burst_index": 2,
            "neighbour_animal": True,
            "edge_score": None,
            "evidence_weights_version": "v1",
        },
        source="fusion",
        source_version="evidence_fusion_v1",
        computed_at="2026-09-29T00:00:00+00:00",
        run_id="r1",
    )
    by_signal = {r["signal"]: r for r in rows}
    assert "edge_score" not in by_signal  # absent: no row, never 0
    assert by_signal["speciesnet_presence"]["value"] == 1.0 and by_signal["speciesnet_presence"]["value_text"] is None
    assert by_signal["gemini_visibility"]["value"] == 0.66 and by_signal["gemini_visibility"]["value_text"] == "partial"
    assert by_signal["burst_id"]["value"] is None and by_signal["burst_id"]["value_text"] == "dep:m1"
    assert by_signal["burst_index"]["value"] == 2.0
    assert by_signal["neighbour_animal"]["value"] == 1.0
    assert by_signal["evidence_weights_version"]["value_text"] == "v1"
    row = by_signal["speciesnet_presence"]
    assert set(row) == {"media_id", "deployment_id", "signal", "value", "value_text", "source", "source_version", "computed_at", "run_id"}
    assert (row["media_id"], row["deployment_id"], row["source"], row["source_version"], row["run_id"]) == (
        "m1",
        "d1",
        "fusion",
        "evidence_fusion_v1",
        "r1",
    )
    with pytest.raises(ValueError):
        me.signal_rows("m", "d", {"not_a_signal": 1}, source="fusion", source_version="v", computed_at="t", run_id=None)
    with pytest.raises(ValueError):
        me.signal_rows("m", "d", {"motion_frac": 1}, source="nope", source_version="v", computed_at="t", run_id=None)


def test_write_skips_when_table_missing_and_probes_once():
    svc, calls = _svc(probe_error=_Missing("Could not find the table 'public.media_evidence' in the schema cache"))
    rows = me.signal_rows("m", "d", {"motion_frac": 0.1}, source="motion", source_version="motion_v1", computed_at="t", run_id=None)
    assert me.write_signals(svc, rows, "d") == 0
    assert me.write_signals(svc, rows, "d") == 0
    assert calls["probe"] == 1 and calls["upserts"] == []  # probed once per process, never written
    assert me.table_available(svc) is False and calls["probe"] == 1
    assert me.read_signal(svc, ["m"], "speciesnet_max_conf") == {} and calls["reads"] == 0


def test_write_upserts_on_the_unique_key_when_table_exists():
    svc, calls = _svc()
    rows = me.signal_rows("m", "d", {"motion_frac": 0.1, "burst_len": 3}, source="bursts", source_version="bursts_v1", computed_at="t", run_id="r")
    assert me.write_signals(svc, rows) == 2
    assert me.write_signals(svc, rows) == 2
    assert calls["probe"] == 1
    assert [oc for _, oc in calls["upserts"]] == [me.UPSERT_CONFLICT] * 2
    assert me.write_signals(svc, []) == 0


def test_read_signal_latest_value_per_media():
    svc, calls = _svc(
        rows=[
            {"media_id": "a", "value": 0.1, "computed_at": "2026-01-01T00:00:00+00:00"},
            {"media_id": "a", "value": 0.3, "computed_at": "2026-02-01T00:00:00+00:00"},
            {"media_id": "b", "value": None, "computed_at": "2026-02-01T00:00:00+00:00"},
        ]
    )
    assert me.read_signal(svc, ["a", "b"], "speciesnet_max_conf", source="speciesnet") == {"a": 0.3}
    assert me.read_signal(svc, [], "speciesnet_max_conf") == {}


def test_other_probe_errors_do_not_cache():
    svc, calls = _svc(probe_error=ConnectionError("boom"))
    assert me.table_available(svc) is False
    assert me.table_available(svc) is False
    assert calls["probe"] == 2  # a transient failure is retried next time
