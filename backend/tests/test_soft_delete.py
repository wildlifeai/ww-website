# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Soft-delete feature: role gates + cascade helpers."""

from unittest.mock import MagicMock

import pytest


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
    """Mock create_service_client so _resolve_org_project + _fetch_active_roles work.
    ``org_id=None`` stands for a project that does not exist."""
    client = MagicMock()

    def table(name):
        if name == "projects":
            return _chain([{"organisation_id": org_id}] if org_id else [])
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


# The database's rule (soft_delete_project): a project_admin of the project, or ww_admin.
# Organisation managers, members, viewers and other system-scope roles are refused.
@pytest.mark.parametrize(
    ("roles", "allowed"),
    [
        ([_role("project_admin", "project", "proj-1")], True),
        ([_role("ww_admin", "system", None)], True),
        ([_role("project_admin", "project", "proj-2")], False),
        ([_role("project_member", "project", "proj-1")], False),
        ([_role("project_viewer", "project", "proj-1")], False),
        ([_role("organisation_manager", "organisation", "org-1")], False),
        ([_role("system_manager", "system", None)], False),
        ([], False),
    ],
)
def test_may_delete_project_matches_database_rule(roles, allowed):
    from app.authz import _may_delete_project

    assert _may_delete_project(roles, "proj-1") is allowed


def test_may_delete_project_ignores_expired_role():
    from app.authz import _may_delete_project

    expired = {**_role("project_admin", "project", "proj-1"), "expires_at": "2000-01-01T00:00:00Z"}
    assert _may_delete_project([expired], "proj-1") is False


@pytest.mark.parametrize(
    ("org_id", "roles", "right"),
    [
        ("org-1", [_role("project_admin", "project", "proj-1")], "allowed"),
        ("org-1", [_role("ww_admin", "system", None)], "allowed"),
        ("org-1", [_role("organisation_manager", "organisation", "org-1")], "refused"),
        ("org-1", [_role("project_member", "project", "proj-1")], "refused"),
        ("org-1", [_role("organisation_manager", "organisation", "org-2")], "not_found"),
        (None, [_role("ww_admin", "system", None)], "not_found"),
    ],
)
async def test_project_restore_right(monkeypatch, org_id, roles, right):
    from app.authz import project_restore_right

    _roles_client(monkeypatch, org_id=org_id, roles=roles)
    assert await project_restore_right("u1", "proj-1") == right


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


def _project_user_client(visible=True, rpc_error=None):
    """A user-session client for one project: RLS shows it when ``visible``; ``soft_delete_project``
    raises ``rpc_error`` (a Postgres code) when given. Records the ids sent to the function."""
    calls: list[str] = []
    client = MagicMock()
    client.table.side_effect = lambda name: _chain([{"id": "proj-1"}] if visible else [])

    def rpc(name, params):
        assert name == "soft_delete_project"
        calls.append(params["p_id"])
        call = MagicMock()
        if rpc_error:
            call.execute.side_effect = _DbError(rpc_error)
        return call

    client.rpc.side_effect = rpc
    return client, calls


def test_delete_project_as_user_cascades_what_the_database_deleted():
    from app.domain.soft_delete import soft_delete_project_as_user

    user, rpc_calls = _project_user_client()
    svc, svc_calls = _recording_client(dep_rows=[{"id": "d1"}, {"id": "d2"}])
    assert soft_delete_project_as_user(user, svc, "proj-1", "ts") == "deleted"
    assert rpc_calls == ["proj-1"]
    # The project gets the shared ts, then its deployments and their children.
    assert svc_calls[0] == ("projects", {"deleted_at": "ts"})
    assert {name for name, _ in svc_calls} == {"projects", "deployments", "media", "observations"}
    assert all(payload == {"deleted_at": "ts"} for _, payload in svc_calls)


@pytest.mark.parametrize(
    ("visible", "rpc_error", "outcome", "rpc_called"),
    [
        (True, "42501", "refused", True),  # an organisation manager, member or viewer
        (True, "P0002", "not_found", True),  # deleted since the visibility read
        (False, None, "not_found", False),  # RLS hides it: no role, unknown or already deleted
    ],
)
def test_delete_project_as_user_cascades_nothing_when_not_deleted(visible, rpc_error, outcome, rpc_called):
    from app.domain.soft_delete import soft_delete_project_as_user

    user, rpc_calls = _project_user_client(visible, rpc_error)
    svc, svc_calls = _recording_client(dep_rows=[{"id": "d1"}])
    assert soft_delete_project_as_user(user, svc, "proj-1", "ts") == outcome
    assert bool(rpc_calls) is rpc_called
    assert svc_calls == []


def test_delete_project_as_user_surfaces_an_unexpected_error():
    from app.domain.soft_delete import soft_delete_project_as_user

    user, _ = _project_user_client(rpc_error="08006")
    svc, svc_calls = _recording_client()
    with pytest.raises(_DbError):
        soft_delete_project_as_user(user, svc, "proj-1", "ts")
    assert svc_calls == []


def test_restore_project_clears_deleted_at():
    from app.domain.soft_delete import restore_project

    client, calls = _recording_client(dep_rows=[{"id": "d1"}])
    restore_project(client, "proj-1", "ts")
    assert ("projects", {"deleted_at": None}) in calls


# ── Routes: deployment and project delete/restore refusals, backfill is admin-only


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
    monkeypatch.setattr("app.routers.projects.create_service_client", lambda: MagicMock())
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


_PROJECT = "11111111-2222-3333-4444-555555555555"


# Delete runs soft_delete_project as the caller, so the user client stands in for the database.
@pytest.mark.parametrize(
    ("visible", "rpc_error", "status"),
    [
        (True, None, 200),  # project_admin or ww_admin
        (True, "42501", 403),  # organisation manager who is not a project admin, member, viewer
        (False, None, 404),  # no role reaching the project
        (True, "P0002", 404),
    ],
)
def test_project_delete_status_follows_the_database(api, monkeypatch, visible, rpc_error, status):
    from app.dependencies import get_user_client
    from app.main import app

    user, _ = _project_user_client(visible, rpc_error)
    svc, svc_calls = _recording_client()
    app.dependency_overrides[get_user_client] = lambda: user
    monkeypatch.setattr("app.routers.projects.create_service_client", lambda: svc)

    resp = api.delete(f"/api/projects/{_PROJECT}")
    assert resp.status_code == status
    if status == 200:
        assert resp.json()["deleted_at"] and svc_calls
    else:
        assert svc_calls == []


def test_project_delete_rejects_a_malformed_id(api):
    assert api.delete("/api/projects/not-a-uuid").status_code == 404


@pytest.mark.parametrize(
    ("roles", "status"),
    [
        ([_role("project_admin", "project", _PROJECT)], 200),
        ([_role("ww_admin", "system", None)], 200),
        ([_role("organisation_manager", "organisation", "org-1")], 403),
        ([_role("project_member", "project", _PROJECT)], 403),
        ([_role("organisation_manager", "organisation", "org-2")], 404),
    ],
)
def test_project_restore_by_role(api, monkeypatch, roles, status):
    restored: list = []
    _roles_client(monkeypatch, org_id="org-1", roles=roles)
    monkeypatch.setattr("app.routers.projects.restore_project", lambda svc, pid, ts: restored.append((pid, ts)))

    resp = api.post(f"/api/projects/{_PROJECT}/restore", json={"deleted_at": "ts"})
    assert resp.status_code == status
    assert restored == ([(_PROJECT, "ts")] if status == 200 else [])


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
