# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Similar photos stay inside the caller's access (#344).

``match_media_embeddings`` runs as the service role, which bypasses RLS, so before the
Brain was on by default ``GET /api/brain/similar`` ranked every organisation's photos and
returned their media and deployment ids. The search is now limited to deployments in the
anchor photo's organisation that the caller may read.
"""

from types import SimpleNamespace

import pytest

from app import authz
from tests.fake_postgrest import FakeDB

ORG, OTHER_ORG = "org-1", "org-2"


def _db(roles):
    return FakeDB(
        {
            "projects": [
                {"id": "p1", "organisation_id": ORG},
                {"id": "p2", "organisation_id": ORG},
                {"id": "p9", "organisation_id": OTHER_ORG},
            ],
            "deployments": [
                {"id": "d1", "project_id": "p1"},
                {"id": "d2", "project_id": "p2"},
                {"id": "d3", "project_id": "p1", "deleted_at": "2026-10-01T00:00:00Z"},
                {"id": "d9", "project_id": "p9"},
            ],
            "media": [{"id": "m1", "deployment_id": "d1"}],
            "user_roles": [{"user_id": "u1", "is_active": True, **r} for r in roles],
        }
    )


@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        ([{"role": "organisation_member", "scope_type": "organisation", "scope_id": ORG}], ["d1", "d2"]),
        ([{"role": "project_member", "scope_type": "project", "scope_id": "p1"}], ["d1"]),
        ([{"role": "ww_admin", "scope_type": "system", "scope_id": None}], ["d1", "d2"]),
        ([], []),
    ],
)
async def test_media_org_deployment_ids(monkeypatch, roles, expected):
    monkeypatch.setattr(authz, "create_service_client", _db(roles).client)
    assert await authz.media_org_deployment_ids("u1", "m1") == expected


async def test_unknown_media_has_no_scope(monkeypatch):
    monkeypatch.setattr(authz, "create_service_client", _db([{"scope_type": "system", "scope_id": None}]).client)
    assert await authz.media_org_deployment_ids("u1", "nope") == []


async def test_an_empty_scope_matches_nothing_rather_than_everything(monkeypatch):
    import app.services.vector_store as vs

    calls = []

    class _Client:
        def rpc(self, name, params):
            calls.append(params)
            return SimpleNamespace(execute=lambda: SimpleNamespace(data=[]))

    monkeypatch.setattr(vs, "create_service_client", _Client)
    await vs.PgVectorService("dinov3-vits").search([0.0] * 384, conditions={"deployment_id": []})
    assert calls[0]["p_deployment_ids"] == []  # uuid[] '{}': no rows, where None would mean no filter


def test_similar_route_searches_only_the_scoped_deployments(monkeypatch):
    from fastapi.testclient import TestClient

    from app.authz import require_media_access
    from app.dependencies import get_current_user
    from app.main import app
    from app.routers import brain
    from app.services import vector_store

    class _Store:
        async def retrieve_vector(self, media_id):
            return [0.0] * 384

        async def search(self, vector, limit, conditions=None, exclude_media_id=None):
            searched.append((limit, conditions, exclude_media_id))
            return [vector_store.SimilarPoint(id="m2", score=0.9, payload={"deployment_id": "d1", "cluster_id": 0})]

    async def _scope(user_id, media_id):
        return ["d1", "d2"]

    searched = []
    monkeypatch.setattr(brain, "create_service_client", lambda: _NoRunClient())
    monkeypatch.setattr(brain, "media_org_deployment_ids", _scope)
    monkeypatch.setattr(vector_store, "get_vector_service", lambda model=None: _Store())
    overrides = {get_current_user: lambda: SimpleNamespace(id="u1", email="t@ww.ai"), require_media_access: lambda: None}
    app.dependency_overrides.update(overrides)
    try:
        r = TestClient(app).get("/api/brain/similar/m1?n=5")
    finally:
        for dep in overrides:
            app.dependency_overrides.pop(dep, None)
    assert r.status_code == 200
    assert r.json()["data"]["results"][0]["media_id"] == "m2"
    assert searched == [(5, {"deployment_id": ["d1", "d2"]}, "m1")]


class _NoRunClient:
    """Service client whose media_embeddings lookup finds no run (default model)."""

    def table(self, name):
        q = SimpleNamespace()
        for attr in ("select", "eq", "maybe_single"):
            setattr(q, attr, lambda *a, **k: q)
        q.execute = lambda: SimpleNamespace(data=None)
        return q
