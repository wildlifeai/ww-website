# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Admin device list (#343): system admins only, joined shape, pages past 1,000 rows."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


class _Query:
    """A PostgREST query over a fixed table that honours ``.range()`` and the 1,000-row cap."""

    def __init__(self, rows, calls):
        self._rows = rows
        self._calls = calls

    def select(self, *_a):
        return self

    def is_(self, column, value):
        assert (column, value) == ("deleted_at", "null")
        return self

    def order(self, column):
        assert column == "id"
        return self

    def range(self, start, end):
        self._calls.append((start, end))
        self._page = self._rows[start : min(end + 1, start + 1000)]
        return self

    def execute(self):
        return SimpleNamespace(data=self._page)


def _svc(devices, deployments):
    calls = {"devices": [], "deployments": []}
    tables = {"devices": devices, "deployments": deployments}

    class _Svc:
        def table(self, name):
            return _Query(tables[name], calls[name])

    return _Svc(), calls


def _device(i, org=("org-1", "Org One")):
    return {
        "id": f"dev-{i:05d}",
        "name": f"Cam {i:05d}",
        "bluetooth_id": f"bt-{i}",
        "device_eui": None,
        "organisations": {"id": org[0], "name": org[1]} if org else None,
    }


def _deployment(dep_id, device_id, start, project=("proj-1", "Project One"), project_deleted=None):
    return {
        "id": dep_id,
        "name": f"Deployment {dep_id}",
        "device_id": device_id,
        "deployment_start": start,
        "deployment_end": None,
        "projects": {"id": project[0], "name": project[1], "deleted_at": project_deleted},
    }


# ── Domain ───────────────────────────────────────────────────────────────────


def test_list_devices_pages_past_the_row_cap():
    from app.domain.admin_devices import list_devices

    devices = [_device(i) for i in range(2500)]
    deployments = [_deployment(f"dep-{i:05d}", f"dev-{i:05d}", "2026-01-01T00:00:00+00:00") for i in range(1200)]
    svc, calls = _svc(devices, deployments)

    rows = list_devices(svc)

    assert len(rows) == 2500
    assert calls["devices"] == [(0, 999), (1000, 1999), (2000, 2999)]
    assert calls["deployments"] == [(0, 999), (1000, 1999)]
    assert sum(1 for r in rows if r["latest_deployment"]) == 1200


def test_list_devices_joins_organisation_and_latest_live_deployment():
    from app.domain.admin_devices import list_devices

    devices = [_device(1), _device(2, org=None)]
    deployments = [
        _deployment("old", "dev-00001", "2025-01-01T00:00:00+00:00"),
        _deployment("new", "dev-00001", "2026-03-01T00:00:00+00:00", project=("proj-2", "Project Two")),
        # Newer still, but its project is deleted, so it is not the device's deployment.
        _deployment("gone", "dev-00001", "2026-06-01T00:00:00+00:00", project_deleted="2026-07-01T00:00:00+00:00"),
    ]
    svc, _ = _svc(devices, deployments)

    rows = list_devices(svc)

    assert rows[0] == {
        "id": "dev-00001",
        "name": "Cam 00001",
        "bluetooth_id": "bt-1",
        "device_eui": None,
        "organisation": {"id": "org-1", "name": "Org One"},
        "latest_deployment": {
            "id": "new",
            "name": "Deployment new",
            "deployment_start": "2026-03-01T00:00:00+00:00",
            "deployment_end": None,
            "project": {"id": "proj-2", "name": "Project Two"},
        },
    }
    assert rows[1]["organisation"] is None and rows[1]["latest_deployment"] is None


# ── Route ────────────────────────────────────────────────────────────────────


@pytest.fixture
def api():
    from app.dependencies import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u1")
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_route_refuses_an_unauthenticated_caller(client):
    assert client.get("/api/admin/devices", headers={"Authorization": "Basic x"}).status_code == 401


def test_route_refuses_a_non_admin(api, monkeypatch):
    async def not_admin(user_id):
        return False

    def no_service_client():
        raise AssertionError("a refused call must not reach the service role")

    monkeypatch.setattr("app.authz.is_system_admin", not_admin)
    monkeypatch.setattr("app.routers.admin.create_service_client", no_service_client)
    assert api.get("/api/admin/devices").status_code == 403


def test_route_returns_the_joined_list_for_a_system_admin(api, monkeypatch):
    async def admin(user_id):
        return True

    svc, _ = _svc([_device(1)], [_deployment("d1", "dev-00001", "2026-01-01T00:00:00+00:00")])
    monkeypatch.setattr("app.authz.is_system_admin", admin)
    monkeypatch.setattr("app.routers.admin.create_service_client", lambda: svc)

    resp = api.get("/api/admin/devices")

    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["total"] == 1
    assert body["data"][0]["organisation"] == {"id": "org-1", "name": "Org One"}
    assert body["data"][0]["latest_deployment"]["project"] == {"id": "proj-1", "name": "Project One"}
