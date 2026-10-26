# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Conservation-intelligence reads page past PostgREST's 1,000-row cap (UI audit 04 #9).

Shift detection, occupancy and species accumulation read without ``.range()``, so they used
the first 1,000 photos or observations of a deployment or project and said nothing.
"""

from types import SimpleNamespace

import pytest

from app.domain import intelligence

N = 1500


class _Query:
    """One query: chains like supabase-py and, like PostgREST, returns 1,000 rows at most."""

    def __init__(self, db: "_DB", table: str):
        self.db, self.table, self.bounds = db, table, None

    def __getattr__(self, _name):  # select, eq, in_, is_: chain
        return lambda *a, **k: self

    def order(self, column, **_kwargs):
        self.db.orders.append((self.table, column))
        return self

    def range(self, start: int, end: int):
        self.bounds = (start, end)
        return self

    def insert(self, row):
        self.db.inserted.append((self.table, row))
        return self

    def execute(self):
        rows = self.db.tables.get(self.table, [])
        start, end = self.bounds or (0, 999)
        return SimpleNamespace(data=rows[start : min(end, start + 999) + 1])


class _DB:
    def __init__(self, tables: dict[str, list[dict]]):
        self.tables = tables
        self.orders: list[tuple[str, str]] = []
        self.inserted: list[tuple[str, dict]] = []

    def table(self, name: str) -> _Query:
        return _Query(self, name)


@pytest.fixture
def use_db(monkeypatch):
    def install(tables):
        db = _DB(tables)
        monkeypatch.setattr("app.services.supabase_client.create_service_client", lambda: db)
        return db

    return install


async def test_shift_detection_counts_every_photo(use_db):
    # The first 1,000 photos are all in period A: an unpaged read never reached period B.
    embedding = [{"cluster_id": 1, "is_outlier": False}]
    media = [{"id": f"m{i:05d}", "timestamp": "2026-01-15" if i < 1000 else "2026-03-15", "media_embeddings": embedding} for i in range(N)]
    db = use_db({"media": media})
    out = await intelligence.detect_distribution_shift("d1", ("2026-01-01", "2026-01-31"), ("2026-03-01", "2026-03-31"))
    assert (out["period_a_count"], out["period_b_count"]) == (1000, N - 1000)
    assert ("media", "id") in db.orders
    assert [t for t, _ in db.inserted] == ["ecological_shift_reports"]


async def test_occupancy_sees_every_observation(use_db):
    # d2's observations all come after the first 1,000 rows.
    obs = [{"deployment_id": "d1" if i < 1000 else "d2", "scientific_name": "Rattus rattus"} for i in range(N)]
    db = use_db({"deployments": [{"id": "d1"}, {"id": "d2"}], "observations": obs})
    out = await intelligence.occupancy("p1")
    assert out["deployments"] == 2
    assert out["pairs"] == [{"a": "d1", "b": "d2", "jaccard": 1.0}]
    assert ("observations", "id") in db.orders


async def test_accumulation_reads_every_observation(use_db):
    obs = [{"scientific_name": f"Species {i}", "classification_timestamp": None, "created_at": f"2026-01-01T00:00:00.{i:06d}"} for i in range(N)]
    db = use_db({"observations": obs})
    out = await intelligence.accumulation("d1")
    assert (out["n_observations"], out["total_species"]) == (N, N)
    assert ("observations", "id") in db.orders
