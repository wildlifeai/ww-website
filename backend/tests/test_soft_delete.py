# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Soft-delete feature: role gates + cascade helpers."""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


class _Result:
    def __init__(self, data):
        self.data = data


def _chain(rows):
    t = MagicMock()
    for m in ("select", "eq", "in_", "is_", "limit"):
        getattr(t, m).return_value = t
    t.execute.return_value = _Result(rows)
    return t


def _roles_client(monkeypatch, *, org_id, roles):
    """Mock create_service_client so _resolve_org_project + _fetch_active_roles work."""
    client = MagicMock()

    def table(name):
        if name == "projects":
            return _chain([{"organisation_id": org_id}])
        if name == "user_roles":
            return _chain(roles)
        return _chain([])

    client.table.side_effect = table
    monkeypatch.setattr("app.authz.create_service_client", lambda: client)


# ── Role gates ───────────────────────────────────────────────────────────────


def _role(role, scope_type, scope_id):
    return {"role": role, "scope_type": scope_type, "scope_id": scope_id, "expires_at": None}


# The database's rule (soft_delete_deployment, ww-backend #266): the creator while a
# project_member, any project_admin, or ww_admin. Organisation managers, viewers and other
# system-scope roles are refused.
@pytest.mark.parametrize(
    ("roles", "setup_by", "allowed"),
    [
        ([_role("project_member", "project", "proj-1")], "u1", True),
        ([_role("project_member", "project", "proj-1")], "someone-else", False),
        ([_role("project_admin", "project", "proj-1")], "someone-else", True),
        ([_role("project_viewer", "project", "proj-1")], "u1", False),
        ([_role("project_member", "project", "proj-2")], "u1", False),
        ([_role("organisation_manager", "organisation", "org-1")], "u1", False),
        ([_role("ww_admin", "system", None)], "someone-else", True),
        ([_role("system_manager", "system", None)], "someone-else", False),
        ([], "u1", False),
    ],
)
def test_may_delete_deployment_matches_database_rule(roles, setup_by, allowed):
    from app.authz import _may_delete_deployment

    assert _may_delete_deployment(roles, "u1", setup_by, "proj-1") is allowed


def test_may_delete_deployment_ignores_expired_role():
    from app.authz import _may_delete_deployment

    expired = {**_role("project_admin", "project", "proj-1"), "expires_at": "2000-01-01T00:00:00Z"}
    assert _may_delete_deployment([expired], "u1", "u1", "proj-1") is False


async def test_split_deployments_by_delete_right(monkeypatch):
    from app.authz import split_deployments_by_delete_right

    roles = [_role("project_member", "project", "proj-1")]
    deps = [
        {"id": "mine", "project_id": "proj-1", "setup_by": "u1"},
        {"id": "theirs", "project_id": "proj-1", "setup_by": "u2"},
    ]
    client = MagicMock()
    client.table.side_effect = lambda name: _chain(roles) if name == "user_roles" else _chain(deps)
    monkeypatch.setattr("app.authz.create_service_client", lambda: client)

    assert await split_deployments_by_delete_right("u1", ["mine", "theirs", "missing"]) == (["mine"], ["theirs"])


async def test_assert_project_admin_denies_member(monkeypatch):
    from app.authz import assert_project_admin

    _roles_client(
        monkeypatch,
        org_id="org-1",
        roles=[
            {"role": "project_member", "scope_type": "project", "scope_id": "proj-1", "expires_at": None},
        ],
    )
    with pytest.raises(HTTPException):
        await assert_project_admin("u1", "proj-1")


async def test_assert_project_admin_allows_org_manager(monkeypatch):
    from app.authz import assert_project_admin

    _roles_client(
        monkeypatch,
        org_id="org-1",
        roles=[
            {"role": "organisation_manager", "scope_type": "organisation", "scope_id": "org-1", "expires_at": None},
        ],
    )
    await assert_project_admin("u1", "proj-1")  # org-manager of the project's org → allowed


# ── Cascade helpers ──────────────────────────────────────────────────────────


def _recording_client(dep_rows=None):
    """A svc client recording every .update(payload) call as (table_name, payload)."""
    calls: list[tuple[str, dict]] = []

    def table(name):
        t = MagicMock()
        for m in ("select", "eq", "in_", "is_"):
            getattr(t, m).return_value = t
        t.execute.return_value = _Result(dep_rows or [])

        def _update(payload):
            calls.append((name, payload))
            u = MagicMock()
            for m in ("in_", "eq", "is_"):
                getattr(u, m).return_value = u
            u.execute.return_value = _Result([])
            return u

        t.update.side_effect = _update
        return t

    client = MagicMock()
    client.table.side_effect = table
    return client, calls


def test_soft_delete_deployments_sets_deleted_at_on_children():
    from app.domain.soft_delete import soft_delete_deployments

    client, calls = _recording_client()
    soft_delete_deployments(client, ["d1", "d2"], "2026-07-01T00:00:00Z")
    tables = {name for name, _ in calls}
    assert tables == {"observations", "media", "deployments"}
    assert all(payload == {"deleted_at": "2026-07-01T00:00:00Z"} for _, payload in calls)


def test_soft_delete_deployments_noop_on_empty():
    from app.domain.soft_delete import soft_delete_deployments

    client, calls = _recording_client()
    soft_delete_deployments(client, [], "ts")
    assert calls == []


def test_soft_delete_project_cascades_to_deployments():
    from app.domain.soft_delete import soft_delete_project

    # The deployments select returns two child deployments to cascade into.
    client, calls = _recording_client(dep_rows=[{"id": "d1"}, {"id": "d2"}])
    soft_delete_project(client, "proj-1", "ts")
    tables = [name for name, _ in calls]
    assert "observations" in tables and "media" in tables
    assert "deployments" in tables and "projects" in tables


class _DbError(Exception):
    """Stands in for postgrest's APIError, which carries the Postgres code."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _user_client(visible_ids, rpc_errors=None):
    """A user-session client: RLS shows ``visible_ids``; ``rpc_errors`` maps id -> Postgres code."""
    rpc_errors = rpc_errors or {}
    calls: list[str] = []
    client = MagicMock()
    client.table.side_effect = lambda name: _chain([{"id": i} for i in visible_ids])

    def rpc(name, params):
        assert name == "soft_delete_deployment"
        calls.append(params["p_id"])
        call = MagicMock()
        code = rpc_errors.get(params["p_id"])
        if code:
            call.execute.side_effect = _DbError(code)
        return call

    client.rpc.side_effect = rpc
    return client, calls


def test_delete_as_user_lets_the_database_decide():
    from app.domain.soft_delete import soft_delete_deployments_as_user

    user, rpc_calls = _user_client(["mine", "theirs"], {"theirs": "42501"})
    svc, svc_calls = _recording_client()
    deleted, refused = soft_delete_deployments_as_user(user, svc, ["mine", "theirs", "hidden", "mine"], "ts")

    assert (deleted, refused) == (["mine"], ["theirs"])
    assert rpc_calls == ["mine", "theirs"]  # an id RLS hides is never sent; duplicates once
    # The cascade stamps the shared ts on the deployment and its children only.
    assert {name for name, _ in svc_calls} == {"deployments", "media", "observations"}
    assert all(payload == {"deleted_at": "ts"} for _, payload in svc_calls)


def test_delete_as_user_all_refused_cascades_nothing():
    from app.domain.soft_delete import soft_delete_deployments_as_user

    user, _ = _user_client(["theirs"], {"theirs": "42501"})
    svc, svc_calls = _recording_client()
    assert soft_delete_deployments_as_user(user, svc, ["theirs"], "ts") == ([], ["theirs"])
    assert svc_calls == []


def test_delete_as_user_cascades_before_an_unexpected_error_surfaces():
    from app.domain.soft_delete import soft_delete_deployments_as_user

    user, _ = _user_client(["a", "b"], {"b": "08006"})
    svc, svc_calls = _recording_client()
    with pytest.raises(_DbError):
        soft_delete_deployments_as_user(user, svc, ["a", "b"], "ts")
    assert {name for name, _ in svc_calls} == {"deployments", "media", "observations"}


def test_restore_project_clears_deleted_at():
    from app.domain.soft_delete import restore_project

    client, calls = _recording_client(dep_rows=[{"id": "d1"}])
    restore_project(client, "proj-1", "ts")
    assert ("projects", {"deleted_at": None}) in calls


# ── Routes: deployment delete/restore report refusals, backfill is admin-only ─


@pytest.fixture
def api(monkeypatch):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from app.dependencies import get_current_user, get_user_client, get_verified_user
    from app.main import app

    user = SimpleNamespace(id="u1", app_metadata={}, email_confirmed_at="2026-01-01T00:00:00Z")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_verified_user] = lambda: user
    app.dependency_overrides[get_user_client] = lambda: MagicMock()
    monkeypatch.setattr("app.routers.deployments.create_service_client", lambda: MagicMock())
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_batch_delete_reports_refused_ids(api, monkeypatch):
    monkeypatch.setattr("app.routers.deployments.soft_delete_deployments_as_user", lambda *a: (["mine"], ["theirs"]))
    resp = api.request("DELETE", "/api/deployments/batch", json={"deployment_ids": ["mine", "theirs"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["deployment_ids"] == ["mine"] and body["refused_ids"] == ["theirs"]
    assert body["deleted_at"]


def test_batch_delete_all_refused_is_403(api, monkeypatch):
    monkeypatch.setattr("app.routers.deployments.soft_delete_deployments_as_user", lambda *a: ([], ["theirs"]))
    resp = api.request("DELETE", "/api/deployments/batch", json={"deployment_ids": ["theirs"]})
    assert resp.status_code == 403
    assert "theirs" in resp.json()["detail"]


def test_batch_restore_restores_only_allowed(api, monkeypatch):
    async def split(user_id, ids):
        return ["mine"], ["theirs"]

    restored_with: list = []

    def restore(svc, ids, ts):
        restored_with.append(ids)
        return ids

    monkeypatch.setattr("app.routers.deployments.split_deployments_by_delete_right", split)
    monkeypatch.setattr("app.routers.deployments.restore_deployments", restore)
    resp = api.post("/api/deployments/batch/restore", json={"deployment_ids": ["mine", "theirs"], "deleted_at": "ts"})
    assert resp.status_code == 200
    assert resp.json() == {"restored": 1, "refused_ids": ["theirs"]}
    assert restored_with == [["mine"]]


def test_backfill_timezones_refuses_non_admin(api, monkeypatch):
    async def not_admin(user_id):
        return False

    def no_service_client():
        raise AssertionError("a refused call must not reach the service role")

    monkeypatch.setattr("app.authz.is_system_admin", not_admin)
    monkeypatch.setattr("app.routers.deployments.create_service_client", no_service_client)
    assert api.post("/api/deployments/backfill-timezones").status_code == 403


def test_backfill_timezones_runs_for_system_admin(api, monkeypatch):
    async def admin(user_id):
        return True

    svc = MagicMock()
    svc.table.side_effect = lambda name: _chain([])
    monkeypatch.setattr("app.authz.is_system_admin", admin)
    monkeypatch.setattr("app.routers.deployments.create_service_client", lambda: svc)
    resp = api.post("/api/deployments/backfill-timezones")
    assert resp.status_code == 200
    assert resp.json() == {"candidates": 0, "updated": 0}


# ── Routes: media delete/restore report which ids changed ────────────────────


def _media_user_client(changed_ids):
    """A user-session client whose media UPDATE returns ``changed_ids``, as RLS would."""
    updates: list[dict] = []
    table = MagicMock()

    def _update(payload):
        updates.append(payload)
        return table

    table.update.side_effect = _update
    for m in ("in_", "eq", "is_"):
        getattr(table, m).return_value = table
    table.execute.return_value = _Result([{"id": i} for i in changed_ids])
    client = MagicMock()
    client.table.return_value = table
    return client, updates


@pytest.fixture
def media_api(api):
    from app.dependencies import get_user_client
    from app.main import app

    holder = {}
    app.dependency_overrides[get_user_client] = lambda: holder["client"]
    return api, holder


# All changed, some skipped (a member asking for someone else's photo), none changed.
_MEDIA_CASES = [
    (["a", "b"], ["a", "b"], []),
    (["b"], ["b"], ["a"]),
    ([], [], ["a", "b"]),
]


@pytest.mark.parametrize(("changed", "done", "skipped"), _MEDIA_CASES)
def test_media_batch_delete_reports_skipped_ids(media_api, changed, done, skipped):
    api, holder = media_api
    holder["client"], updates = _media_user_client(changed)
    resp = api.request("DELETE", "/api/media/batch", json={"media_ids": ["a", "b"]})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["deleted_ids"] == done and data["skipped_ids"] == skipped
    assert data["deleted"] == len(done) and data["requested"] == 2
    assert updates == [{"deleted_at": data["deleted_at"]}]


@pytest.mark.parametrize(("changed", "done", "skipped"), _MEDIA_CASES)
def test_media_batch_restore_reports_skipped_ids(media_api, changed, done, skipped):
    api, holder = media_api
    holder["client"], updates = _media_user_client(changed)
    resp = api.post("/api/media/batch/restore", json={"media_ids": ["a", "b"], "deleted_at": "ts"})
    assert resp.status_code == 200
    assert resp.json()["data"] == {"restored": len(done), "restored_ids": done, "skipped_ids": skipped}
    assert updates == [{"deleted_at": None}]


def test_media_delete_as_user_dedupes_and_skips_empty():
    from app.domain.soft_delete import soft_delete_media_as_user

    client, updates = _media_user_client(["a"])
    assert soft_delete_media_as_user(client, ["a", "b", "a"], "ts") == (["a"], ["b"])
    assert soft_delete_media_as_user(client, [], "ts") == ([], [])
    assert len(updates) == 1  # an empty request sends nothing
