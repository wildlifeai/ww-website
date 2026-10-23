# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""GET /api/qa/report/{deployment_id} reads with the service role, so it checks read access.

It carried no access dependency, so any signed-in user could read any deployment's QA numbers.
The router is mounted only with ``FF_ACTIVE_LEARNING_ENABLED``, so the tests mount it themselves.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import authz
from app.config import settings
from app.dependencies import get_current_user
from app.routers import qa
from tests.fake_postgrest import FakeDB


def _db(roles):
    return FakeDB(
        {
            "projects": [{"id": "p1", "organisation_id": "org-1"}],
            "deployments": [{"id": "d1", "project_id": "p1"}],
            "user_roles": [{"user_id": "u1", "is_active": True, **r} for r in roles],
        }
    )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "FF_ACTIVE_LEARNING_ENABLED", True)
    app = FastAPI()
    app.include_router(qa.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u1", email="u@ww.ai")
    return TestClient(app)


def test_qa_report_is_hidden_from_a_user_without_access(client, monkeypatch):
    monkeypatch.setattr(authz, "create_service_client", _db([]).client)
    reports = []

    async def qa_report(deployment_id):
        reports.append(deployment_id)
        return {}

    monkeypatch.setattr("app.domain.active_learning.qa_report", qa_report)
    r = client.get("/api/qa/report/d1")
    assert r.status_code == 404
    assert not reports


def test_qa_report_is_served_to_a_project_viewer(client, monkeypatch):
    monkeypatch.setattr(authz, "create_service_client", _db([{"role": "project_viewer", "scope_type": "project", "scope_id": "p1"}]).client)

    async def qa_report(deployment_id):
        return {"deployment_id": deployment_id}

    monkeypatch.setattr("app.domain.active_learning.qa_report", qa_report)
    r = client.get("/api/qa/report/d1")
    assert r.status_code == 200
    assert r.json()["data"] == {"deployment_id": "d1"}
