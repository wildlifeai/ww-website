# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Wildlife Brain reads of ``media_embeddings`` page past PostgREST's 1,000-row cap.

A read without ``.range()`` stops at 1,000 rows without an error, so confirming a cluster of
more than 1,000 photos labelled only 1,000 of them (pinned in ``test_human_verdicts.py``), and
UMAP drew only 1,000 points.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.authz import require_deployment_access
from app.dependencies import get_current_user
from app.main import app
from app.routers import brain
from app.services.db_utils import read_all

MEMBERS = [{"media_id": f"m{i:05d}"} for i in range(2345)]


class _Query:
    """One query: chains like supabase-py and, like PostgREST, returns 1,000 rows at most."""

    def __init__(self, table: str):
        self.table, self.bounds = table, None

    def __getattr__(self, _name):  # select, eq, order, ...: chain
        return lambda *a, **k: self

    def range(self, start: int, end: int):
        self.bounds = (start, end)
        return self

    def execute(self):
        if self.table == "media_embeddings":
            start, end = self.bounds or (0, 999)
            return SimpleNamespace(data=MEMBERS[start : min(end, start + 999) + 1])
        return SimpleNamespace(data=[])


class _DB:
    def table(self, name):
        return _Query(name)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(brain, "create_service_client", lambda: _DB())
    overrides = {
        get_current_user: lambda: SimpleNamespace(id="u1", email="u@ww.ai"),
        require_deployment_access: lambda: None,
    }
    app.dependency_overrides.update(overrides)
    yield TestClient(app)
    for dep in overrides:
        app.dependency_overrides.pop(dep, None)


def test_read_all_pages_until_a_short_page():
    assert len(read_all(lambda: _DB().table("media_embeddings").select("media_id").order("media_id"))) == len(MEMBERS)


def test_umap_returns_every_point_past_1000(client):
    r = client.get("/api/brain/umap/d1")
    assert r.status_code == 200
    assert r.json()["data"]["count"] == len(MEMBERS)
