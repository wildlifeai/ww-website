# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for active-learning + QA pure helpers."""

import pytest

from app.domain.active_learning import combine_al_score, compute_qa_metrics


def test_combine_al_score_all_zero():
    assert combine_al_score(0.0, 0.0, 0.0, False) == 0.0


def test_combine_al_score_weights():
    # 0.35*1 + 0.35*1 + 0.20*1 + 0.10*1 = 1.0
    assert combine_al_score(1.0, 1.0, 1.0, True) == 1.0
    # novelty only → 0.35
    assert combine_al_score(1.0, 0.0, 0.0, False) == 0.35
    # uncertainty only → 0.35
    assert combine_al_score(0.0, 1.0, 0.0, False) == 0.35
    # disagreement only → 0.20
    assert combine_al_score(0.0, 0.0, 1.0, False) == 0.20
    # outlier only → 0.10
    assert combine_al_score(0.0, 0.0, 0.0, True) == 0.10


def test_combine_al_score_clamps_inputs():
    # out-of-range inputs are clamped to [0,1]
    assert combine_al_score(5.0, -2.0, 0.0, False) == 0.35  # novelty clamps to 1, uncertainty to 0


def test_qa_metrics_only_counts_pairs_with_both_labels():
    pairs = [
        ("Apteryx mantelli", "Apteryx mantelli"),  # match
        ("Gallirallus", "gallirallus"),  # match (case-insensitive)
        ("Mustela", "Trichosurus"),  # mismatch
        ("Felis catus", None),  # ignored (no human)
        (None, "Sus scrofa"),  # ignored (no ai)
    ]
    m = compute_qa_metrics(pairs)
    assert m["n_compared"] == 3
    assert m["matches"] == 2
    assert m["precision"] == pytest.approx(0.6667, abs=1e-4)


def test_qa_metrics_empty():
    assert compute_qa_metrics([]) == {"n_compared": 0, "matches": 0, "precision": None}
    assert compute_qa_metrics([(None, None), ("x", None)])["precision"] is None


# ── recompute_scores persistence ─────────────────────────────────────


class _Result:
    def __init__(self, data):
        self.data = data


async def test_recompute_scores_bulk_upserts_in_chunks(monkeypatch):
    """Scores are persisted via chunked bulk upserts, not one update per row, for every embedding:
    1,200 of them, so the read has to page past PostgREST's 1,000."""
    from unittest.mock import MagicMock

    from app.domain.active_learning import recompute_scores

    embeddings = [{"media_id": f"m{i}", "cluster_confidence": 0.5, "is_outlier": False} for i in range(1200)]
    upsert_payloads = []

    me_table = MagicMock()
    me_table.select.return_value = me_table
    me_table.eq.return_value = me_table
    me_table.is_.return_value = me_table
    me_table.order.return_value = me_table
    # Like PostgREST, one page of the range asked for (the read stops after a short page).
    me_table.range.side_effect = lambda start, end: MagicMock(execute=lambda: _Result(embeddings[start : end + 1]))

    def _upsert(payload):
        upsert_payloads.append(payload)
        chained = MagicMock()
        chained.execute.return_value = _Result([])
        return chained

    me_table.upsert.side_effect = _upsert

    obs_table = MagicMock()
    obs_table.select.return_value = obs_table
    obs_table.eq.return_value = obs_table
    obs_table.is_.return_value = obs_table
    obs_table.order.return_value = obs_table
    obs_table.range.return_value = obs_table
    obs_table.execute.return_value = _Result([])

    client = MagicMock()
    client.table.side_effect = lambda name: me_table if name == "media_embeddings" else obs_table
    monkeypatch.setattr("app.services.supabase_client.create_service_client", lambda: client)

    updated = await recompute_scores("dep-1")

    assert updated == 1200
    assert [len(p) for p in upsert_payloads] == [500, 500, 200]
    # Only the score columns are supplied — never enough to insert a new row.
    assert set(upsert_payloads[0][0]) == {"media_id", "active_learning_score", "al_score_updated_at"}


# ── QA reports read every page ───────────────────────────────────────


def _photo_rows(deployment_id: str, n: int, agree: int, prefix: str) -> list[dict]:
    """``n`` photos, each with an AI row and a human row; the first ``agree`` humans agree."""
    rows = []
    for i in range(n):
        media_id = f"{prefix}{i:05d}"
        base = {"deployment_id": deployment_id, "media_id": media_id, "vernacular_name": None}
        rows.append(
            {
                **base,
                "id": f"{media_id}-ai",
                "source_type": "ai",
                "review_status": "unreviewed",
                "confidence": 0.9,
                "scientific_name": "Rattus rattus",
            }
        )
        human = "Rattus rattus" if i < agree else "Mus musculus"
        rows.append({**base, "id": f"{media_id}-h", "source_type": "human", "review_status": "human_reviewed", "scientific_name": human})
    return rows


def _qa_db():
    from tests.fake_postgrest import FakeDB

    ai_only = [{"id": "x-ai", "deployment_id": "d3", "media_id": "x", "source_type": "ai", "confidence": 0.5, "scientific_name": "Felis catus"}]
    return FakeDB(
        {
            "deployments": [
                {"id": "d1", "project_id": "p1", "location_name": "Ridge"},
                {"id": "d2", "project_id": "p1", "location_name": "Creek"},
                {"id": "d3", "project_id": "p1", "location_name": "Hut"},
                {"id": "d4", "project_id": "p1", "location_name": "Gone", "deleted_at": "2026-10-01"},
                {"id": "d5", "project_id": "p2", "location_name": "Elsewhere"},
            ],
            "observations": _photo_rows("d1", 1200, 1000, "a")
            + _photo_rows("d2", 3, 3, "b")
            + ai_only
            + _photo_rows("d4", 5, 5, "c")
            + _photo_rows("d5", 5, 0, "e"),
        }
    )


async def test_qa_report_reads_every_observation_past_1000(monkeypatch):
    from app.domain.active_learning import qa_report

    monkeypatch.setattr("app.services.supabase_client.create_service_client", _qa_db().client)
    report = await qa_report("d1")
    assert (report["n_compared"], report["matches"]) == (1200, 1000)


async def test_project_qa_report_adds_up_the_live_deployments(monkeypatch):
    from app.domain.active_learning import qa_project_report

    monkeypatch.setattr("app.services.supabase_client.create_service_client", _qa_db().client)
    report = await qa_project_report("p1")

    assert (report["n_compared"], report["matches"]) == (1203, 1003)
    assert report["precision"] == pytest.approx(1003 / 1203, abs=1e-4)
    # Only deployments with a photo compared, most compared first; the deleted one is left out.
    assert [(p["deployment_id"], p["location_name"], p["n_compared"], p["matches"]) for p in report["per_deployment"]] == [
        ("d1", "Ridge", 1200, 1000),
        ("d2", "Creek", 3, 3),
    ]


async def test_project_qa_report_without_comparisons_has_no_precision(monkeypatch):
    from app.domain.active_learning import qa_project_report
    from tests.fake_postgrest import FakeDB

    monkeypatch.setattr("app.services.supabase_client.create_service_client", FakeDB({"deployments": [], "observations": []}).client)
    report = await qa_project_report("p1")
    assert (report["n_compared"], report["precision"], report["per_deployment"]) == (0, None, [])
