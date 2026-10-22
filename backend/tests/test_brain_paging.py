# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Wildlife Brain reads of ``media_embeddings`` page past PostgREST's 1,000-row cap.

A read without ``.range()`` stops at 1,000 rows without an error, so confirming a cluster of
more than 1,000 photos labelled only 1,000 of them, and UMAP drew only 1,000 points.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.authz import require_cluster_write, require_deployment_access
from app.dependencies import get_current_user
from app.main import app
from app.routers import brain
from app.services.db_utils import read_all

MEMBERS = [{"media_id": f"m{i:05d}"} for i in range(2345)]
ASSIGNMENT = {"id": "c1", "deployment_id": "d1", "embedding_run_id": "r1", "cluster_id": 3}


class _Query:
    """One query: chains like supabase-py and, like PostgREST, returns 1,000 rows at most."""

    def __init__(self, db: "_DB", table: str):
        self.db, self.table, self.bounds, self.single = db, table, None, False

    def __getattr__(self, _name):  # select, eq, order, ...: chain
        return lambda *a, **k: self

    def range(self, start: int, end: int):
        self.bounds = (start, end)
        return self

    def maybe_single(self):
        self.single = True
        return self

    def insert(self, rows):
        self.db.inserted.extend(rows)
        return self

    def update(self, payload):
        self.db.updated.append(payload)
        return self

    def execute(self):
        if self.table == "cluster_assignments" and self.single:
            return SimpleNamespace(data=ASSIGNMENT)
        if self.table == "media_embeddings":
            start, end = self.bounds or (0, 999)
            return SimpleNamespace(data=MEMBERS[start : min(end, start + 999) + 1])
        return SimpleNamespace(data=[])


class _DB:
    def __init__(self):
        self.inserted: list = []
        self.updated: list = []

    def table(self, name):
        return _Query(self, name)


@pytest.fixture
def client(monkeypatch):
    db = _DB()
    monkeypatch.setattr(brain, "create_service_client", lambda: db)
    overrides = {
        get_current_user: lambda: SimpleNamespace(id="u1", email="u@ww.ai"),
        require_cluster_write: lambda: None,
        require_deployment_access: lambda: None,
    }
    app.dependency_overrides.update(overrides)
    yield TestClient(app), db
    for dep in overrides:
        app.dependency_overrides.pop(dep, None)


def test_read_all_pages_until_a_short_page():
    db = _DB()
    assert len(read_all(lambda: db.table("media_embeddings").select("media_id").order("media_id"))) == len(MEMBERS)


def test_confirming_a_cluster_labels_every_member_past_1000(client):
    http, db = client
    r = http.post("/api/brain/clusters/c1/confirm", json={"taxon_id": None, "scientific_name": "Rattus rattus"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["observations_created"] == len(MEMBERS)
    assert sorted(o["media_id"] for o in db.inserted) == [m["media_id"] for m in MEMBERS]


def test_umap_returns_every_point_past_1000(client):
    http, _ = client
    r = http.get("/api/brain/umap/d1")
    assert r.status_code == 200
    assert r.json()["data"]["count"] == len(MEMBERS)
