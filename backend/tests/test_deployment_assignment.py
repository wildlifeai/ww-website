# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Manual deployment assignment: access classification + the create-deployment guard.

Covers the security-critical pieces of the upload-assignment feature — that images can't be
attached to (or created against) a deployment/project the caller has no role on.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


class _Result:
    def __init__(self, data):
        self.data = data


def _chain(rows):
    """A MagicMock table whose select/eq/in_/is_/limit all chain and execute() returns rows."""
    t = MagicMock()
    for m in ("select", "eq", "in_", "is_", "limit"):
        getattr(t, m).return_value = t
    t.execute.return_value = _Result(rows)
    return t


# ── classify_deployment_access ───────────────────────────────────────────────


async def test_classify_deployment_access(monkeypatch):
    """valid = caller may add photos (project_member or above on the deployment's project);
    no_access = exists, but they may not; not_found = not in the DB."""
    from app.authz import classify_deployment_access

    roles = [{"role": "project_admin", "scope_type": "project", "scope_id": "proj-1", "expires_at": None}]
    dep_rows = [
        {"id": "dep-valid", "project_id": "proj-1", "projects": {"organisation_id": "org-1"}},
        {"id": "dep-blocked", "project_id": "proj-9", "projects": {"organisation_id": "org-9"}},
    ]

    client = MagicMock()
    client.table.side_effect = lambda name: _chain(roles) if name == "user_roles" else _chain(dep_rows)
    monkeypatch.setattr("app.authz.create_service_client", lambda: client)

    result = await classify_deployment_access("user-1", ["dep-valid", "dep-blocked", "dep-missing"])
    assert result == {"dep-valid": "valid", "dep-blocked": "no_access", "dep-missing": "not_found"}


async def test_classify_deployment_access_empty():
    from app.authz import classify_deployment_access

    assert await classify_deployment_access("user-1", []) == {}


# ── POST /api/deployments (create) access guard ──────────────────────────────


@pytest.fixture
def verified_client():
    from fastapi.testclient import TestClient

    from app.dependencies import get_verified_user
    from app.main import app

    mock_user = MagicMock()
    mock_user.id = "user-1"
    app.dependency_overrides[get_verified_user] = lambda: mock_user
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_create_deployment_denied_for_non_member(verified_client, monkeypatch):
    """A caller with no role on the target project is refused (access guard → 404)."""

    async def _deny(*a, **k):
        raise HTTPException(status_code=404, detail="Not found")

    monkeypatch.setattr("app.routers.deployments.assert_write", _deny)

    resp = verified_client.post("/api/deployments", json={"project_id": "proj-x", "name": "Cam 1"})
    assert resp.status_code == 404


def test_create_deployment_refused_for_a_viewer(verified_client, monkeypatch):
    """A project_viewer reads the project but cannot add to it (assert_write -> 403)."""

    async def _refuse(*a, **k):
        raise HTTPException(status_code=403, detail="You need the Project Member role to change this project.")

    monkeypatch.setattr("app.routers.deployments.assert_write", _refuse)

    resp = verified_client.post("/api/deployments", json={"project_id": "proj-1", "name": "Cam 1"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "You need the Project Member role to change this project."


def test_create_deployment_succeeds_for_member(verified_client, monkeypatch):
    """With project access, a deployment (and its placeholder device) is created."""
    monkeypatch.setattr("app.routers.deployments.assert_write", AsyncMock(return_value=None))

    def make_table(name):
        t = MagicMock()
        for m in ("select", "eq", "limit"):
            getattr(t, m).return_value = t
        t.execute.return_value = _Result([{"id": "proj-1", "organisation_id": "org-1"}] if name == "projects" else [])
        ins = MagicMock()
        ins.execute.return_value = _Result([])
        t.insert.return_value = ins
        return t

    client = MagicMock()
    client.table.side_effect = make_table
    monkeypatch.setattr("app.routers.deployments.create_service_client", lambda: client)

    resp = verified_client.post("/api/deployments", json={"project_id": "proj-1", "name": "North Ridge"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_id"] == "proj-1"
    assert body["name"] == "North Ridge"
    assert "id" in body


# ── POST /api/deployments with a caller-supplied id (ww-website#140) ─────────
#
# The camera stamps the configured deployment's UUID into every frame (EXIF 0xF200).
# When that deployment never reached the cloud (the phone's push was rejected), the
# upload triage creates it under the same id, so the phone's later sync converges on
# the row instead of producing a duplicate.

STAMPED_ID = "e10f7c43-9b90-4f59-bef5-f35b8e698517"


def _tables_with_existing_deployments(existing: list[dict]):
    inserted: list[dict] = []

    def make_table(name):
        t = MagicMock()
        for m in ("select", "eq", "limit"):
            getattr(t, m).return_value = t
        rows = {"projects": [{"id": "proj-1", "organisation_id": "org-1"}], "deployments": existing}.get(name, [])
        t.execute.return_value = _Result(rows)
        ins = MagicMock()
        ins.execute.return_value = _Result([])
        t.insert.side_effect = lambda row: (inserted.append((name, row)), ins)[1]
        return t

    client = MagicMock()
    client.table.side_effect = make_table
    return client, inserted


def test_create_deployment_uses_the_supplied_id(verified_client, monkeypatch):
    monkeypatch.setattr("app.routers.deployments.assert_write", AsyncMock(return_value=None))
    client, inserted = _tables_with_existing_deployments(existing=[])
    monkeypatch.setattr("app.routers.deployments.create_service_client", lambda: client)

    resp = verified_client.post("/api/deployments", json={"project_id": "proj-1", "name": "Bench", "id": STAMPED_ID.upper()})
    assert resp.status_code == 200
    assert resp.json()["id"] == STAMPED_ID  # normalised to canonical lower-case
    dep_rows = [row for name, row in inserted if name == "deployments"]
    assert dep_rows and dep_rows[0]["id"] == STAMPED_ID


def test_create_deployment_refuses_an_existing_id(verified_client, monkeypatch):
    """A duplicate is a 409, never a silent reuse of a row that may belong to someone else."""
    monkeypatch.setattr("app.routers.deployments.assert_write", AsyncMock(return_value=None))
    client, inserted = _tables_with_existing_deployments(existing=[{"id": STAMPED_ID}])
    monkeypatch.setattr("app.routers.deployments.create_service_client", lambda: client)

    resp = verified_client.post("/api/deployments", json={"project_id": "proj-1", "name": "Bench", "id": STAMPED_ID})
    assert resp.status_code == 409
    assert not [row for name, row in inserted if name == "deployments"]


def test_create_deployment_rejects_a_non_uuid_id(verified_client, monkeypatch):
    monkeypatch.setattr("app.routers.deployments.assert_write", AsyncMock(return_value=None))
    resp = verified_client.post("/api/deployments", json={"project_id": "proj-1", "name": "Bench", "id": "E10F7C43"})
    assert resp.status_code == 400
