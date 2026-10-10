# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Soft delete and restore: the database's functions decide, the API maps their answers."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

TS = "2026-10-10T01:02:03.456789+00:00"


class _Result:
    def __init__(self, data):
        self.data = data


def _chain(rows):
    t = MagicMock()
    for m in ("select", "eq", "in_", "is_", "limit"):
        getattr(t, m).return_value = t
    t.execute.return_value = _Result(rows)
    return t


_DEVICE = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class _DbError(Exception):
    """Stands in for postgrest's APIError, which carries the Postgres code. A ``23P01`` carries
    the device id in its details, as the one-open-deployment-per-camera constraint does."""

    def __init__(self, code, details=None):
        super().__init__(code)
        self.code = code
        self.details = details or (f"Key (device_id)=({_DEVICE}) conflicts with existing key (device_id)=({_DEVICE})." if code == "23P01" else None)


def _user_client(visible_ids=(), outcomes=None):
    """A user-session client. RLS shows ``visible_ids``, and the camera ``Gate cam``; ``outcomes``
    maps an id to what the database function answers for it: a Postgres code or an error it
    raises, or the value it returns. Records every function call as ``(name, params)`` and every
    table it reads."""
    outcomes = outcomes or {}
    calls: list[tuple[str, dict]] = []
    tables: list[str] = []
    client = MagicMock()

    def table(name):
        tables.append(name)
        return _chain([{"name": "Gate cam"}] if name == "devices" else [{"id": i} for i in visible_ids])

    def rpc(name, params):
        calls.append((name, params))
        call = MagicMock()
        outcome = outcomes.get(params["p_id"])
        if isinstance(outcome, str):
            call.execute.side_effect = _DbError(outcome)
        elif isinstance(outcome, Exception):
            call.execute.side_effect = outcome
        else:
            call.execute.return_value = _Result(outcome)
        return call

    client.table.side_effect = table
    client.rpc.side_effect = rpc
    client.tables = tables
    return client, calls


# ── Timestamps ───────────────────────────────────────────────────────────────


def test_now_iso_keeps_microseconds_and_utc():
    from app.domain.soft_delete import now_iso

    parsed = datetime.fromisoformat(now_iso())
    assert parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0
    assert now_iso().endswith("+00:00")


@pytest.mark.parametrize("value", [TS, "2026-10-10T01:02:03Z", "2026-10-10T13:02:03.5+12:00"])
def test_check_deleted_at_accepts_an_instant(value):
    from app.domain.soft_delete import check_deleted_at

    assert check_deleted_at(value) == value


@pytest.mark.parametrize("value", ["2026-10-10T01:02:03.456789", "ts", ""])
def test_check_deleted_at_refuses_a_naive_or_malformed_value(value):
    from app.domain.soft_delete import check_deleted_at

    with pytest.raises(ValueError):
        check_deleted_at(value)


# ── Domain: deployments ──────────────────────────────────────────────────────


def test_delete_deployments_as_user_lets_the_database_decide():
    from app.domain.soft_delete import soft_delete_deployments_as_user

    user, calls = _user_client(["mine", "theirs", "gone"], {"theirs": "42501", "gone": "P0002"})
    deleted, refused = soft_delete_deployments_as_user(user, ["mine", "theirs", "hidden", "gone", "mine"], TS)

    assert (deleted, refused) == (["mine"], ["theirs"])
    # An id RLS hides is never sent, duplicates go once, and every call carries the shared ts.
    assert calls == [("soft_delete_deployment", {"p_id": i, "p_deleted_at": TS}) for i in ("mine", "theirs", "gone")]


def test_delete_deployments_as_user_sends_nothing_for_an_empty_request():
    from app.domain.soft_delete import soft_delete_deployments_as_user

    user, calls = _user_client()
    assert soft_delete_deployments_as_user(user, [], TS) == ([], [])
    assert calls == [] and user.tables == []


def test_delete_deployments_as_user_surfaces_an_unexpected_error():
    from app.domain.soft_delete import soft_delete_deployments_as_user

    user, _ = _user_client(["a", "b"], {"b": "08006"})
    with pytest.raises(_DbError):
        soft_delete_deployments_as_user(user, ["a", "b"], TS)


def test_restore_deployments_as_user_lets_the_database_decide():
    from app.domain.soft_delete import restore_deployments_as_user

    user, calls = _user_client(outcomes={"mine": True, "stale": False, "theirs": "42501", "gone": "P0002"})
    restored, refused, cameras = restore_deployments_as_user(user, ["mine", "stale", "theirs", "gone", "mine"], TS)

    assert (restored, refused, cameras) == (["mine"], ["theirs"], [])
    assert calls == [("restore_deployment", {"p_id": i, "p_deleted_at": TS}) for i in ("mine", "stale", "theirs", "gone")]
    # No visibility read: the SELECT policy hides a deleted row from everyone.
    assert user.tables == []


def test_restore_deployments_as_user_names_a_camera_with_another_open_deployment():
    from app.domain.soft_delete import restore_deployments_as_user

    user, calls = _user_client(outcomes={"open": "23P01", "mine": True})
    assert restore_deployments_as_user(user, ["open", "mine"], TS) == (["mine"], [], ["Gate cam"])
    assert [c[1]["p_id"] for c in calls] == ["open", "mine"]  # the others carry on
    assert user.tables == ["devices"]


def test_restore_deployments_as_user_surfaces_an_unexpected_error():
    from app.domain.soft_delete import restore_deployments_as_user

    user, _ = _user_client(outcomes={"a": True, "b": "08006"})
    with pytest.raises(_DbError):
        restore_deployments_as_user(user, ["a", "b"], TS)


# ── Domain: projects ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("visible", "outcome", "result", "called"),
    [
        (True, None, "deleted", True),  # project_admin or ww_admin
        (True, "42501", "refused", True),  # an organisation manager, member or viewer
        (True, "P0002", "not_found", True),  # deleted since the visibility read
        (False, None, "not_found", False),  # RLS hides it: no role, unknown or already deleted
    ],
)
def test_delete_project_as_user_maps_the_database_answer(visible, outcome, result, called):
    from app.domain.soft_delete import soft_delete_project_as_user

    user, calls = _user_client(["proj-1"] if visible else [], {"proj-1": outcome})
    assert soft_delete_project_as_user(user, "proj-1", TS) == result
    assert calls == ([("soft_delete_project", {"p_id": "proj-1", "p_deleted_at": TS})] if called else [])


@pytest.mark.parametrize(
    ("outcome", "result"),
    [(True, "restored"), (False, "unchanged"), ("42501", "refused"), ("P0002", "not_found")],
)
def test_restore_project_as_user_maps_the_database_answer(outcome, result):
    from app.domain.soft_delete import restore_project_as_user

    user, calls = _user_client(outcomes={"proj-1": outcome})
    assert restore_project_as_user(user, "proj-1", TS) == result
    assert calls == [("restore_project", {"p_id": "proj-1", "p_deleted_at": TS})]
    assert user.tables == []


def test_restore_project_as_user_names_a_camera_with_another_open_deployment():
    from app.domain.open_deployments import OpenDeploymentConflict
    from app.domain.soft_delete import restore_project_as_user

    user, _ = _user_client(outcomes={"proj-1": "23P01"})
    with pytest.raises(OpenDeploymentConflict) as err:
        restore_project_as_user(user, "proj-1", TS)
    assert err.value.camera == "Gate cam"


def _reading_service_client(fail=False):
    """A service client for naming a camera: the reopened deployment's camera has another open
    deployment, and the camera is ``Gate cam``. Records every table it touches and any write."""
    touched: list[str] = []
    writes: list[str] = []

    def table(name):
        touched.append(name)
        if fail:
            raise RuntimeError("connection reset")
        t = _chain([{"name": "Gate cam"}] if name == "devices" else [{"device_id": _DEVICE}])
        for m in ("update", "insert", "upsert", "delete"):
            getattr(t, m).side_effect = lambda *a, _m=m, **k: writes.append(_m)
        return t

    svc = MagicMock()
    svc.table.side_effect = table
    return svc, touched, writes


# Under the caller's session RLS hides the key in a 23P01, so the camera is found by a read.
_HIDDEN = "Key conflicts with existing key."


@pytest.mark.parametrize(("fail", "camera"), [(False, "Gate cam"), (True, "unknown")])
def test_restore_names_a_camera_rls_hides_with_a_service_read(monkeypatch, fail, camera):
    from app.domain.open_deployments import OpenDeploymentConflict
    from app.domain.soft_delete import restore_deployments_as_user, restore_project_as_user

    svc, touched, writes = _reading_service_client(fail)
    monkeypatch.setattr("app.domain.soft_delete.create_service_client", lambda: svc)
    user, _ = _user_client(outcomes={"open": _DbError("23P01", _HIDDEN), "proj-1": _DbError("23P01", _HIDDEN)})

    assert restore_deployments_as_user(user, ["open"], TS) == ([], [], [camera])
    with pytest.raises(OpenDeploymentConflict) as err:
        restore_project_as_user(user, "proj-1", TS)
    assert err.value.camera == camera
    assert writes == []
    if not fail:
        assert touched == ["deployments", "deployments", "devices"] * 2


@pytest.mark.parametrize("fn", ["soft_delete_project_as_user", "restore_project_as_user"])
def test_project_functions_surface_an_unexpected_error(fn):
    from app.domain import soft_delete

    user, _ = _user_client(["proj-1"], {"proj-1": "08006"})
    with pytest.raises(_DbError):
        getattr(soft_delete, fn)(user, "proj-1", TS)


# ── Routes: deployment and project delete/restore, backfill is admin-only ───


def _no_service_client():
    raise AssertionError("delete and restore must not use the service role")


@pytest.fixture
def api(monkeypatch):
    """The app with a signed-in user. ``holder["client"]`` is the user's client; the service
    client fails the test, so a route that reaches for it is caught."""
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from app.dependencies import get_current_user, get_user_client, get_verified_user
    from app.main import app

    user = SimpleNamespace(id="u1", app_metadata={}, email_confirmed_at="2026-01-01T00:00:00Z")
    holder = {"client": MagicMock()}
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_verified_user] = lambda: user
    app.dependency_overrides[get_user_client] = lambda: holder["client"]
    monkeypatch.setattr("app.routers.deployments.create_service_client", _no_service_client)
    monkeypatch.setattr("app.routers.projects.create_service_client", _no_service_client)
    client = TestClient(app)
    client.holder = holder
    yield client
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("visible", "outcomes", "status", "deleted", "refused"),
    [
        (["mine", "theirs"], {"theirs": "42501"}, 200, ["mine"], ["theirs"]),
        (["theirs"], {"theirs": "42501"}, 403, None, None),
        (["gone"], {"gone": "P0002"}, 200, [], []),
        ([], {}, 200, [], []),  # RLS hides every id
    ],
)
def test_batch_delete_status_follows_the_database(api, visible, outcomes, status, deleted, refused):
    api.holder["client"], calls = _user_client(visible, outcomes)
    ids = visible or ["hidden"]
    resp = api.request("DELETE", "/api/deployments/batch", json={"deployment_ids": ids})
    assert resp.status_code == status
    if status == 403:
        assert "theirs" in resp.json()["detail"]
        return
    body = resp.json()
    assert body["deployment_ids"] == deleted and body["refused_ids"] == refused
    if deleted:
        # The deleted_at handed back for Undo is the one the database stamped.
        assert {c[1]["p_deleted_at"] for c in calls} == {body["deleted_at"]}
    else:
        assert body["deleted_at"] is None


@pytest.mark.parametrize(
    ("outcomes", "status", "restored", "refused"),
    [
        ({"mine": True}, 200, 1, []),
        ({"mine": True, "theirs": "42501"}, 200, 1, ["theirs"]),
        ({"theirs": "42501"}, 403, None, None),
        ({"gone": "P0002"}, 200, 0, []),
        ({"mine": False}, 200, 0, []),  # nothing deleted at that timestamp
        ({"open": "23P01"}, 409, "Nothing restored", None),  # its camera has another open deployment
        ({"mine": True, "open": "23P01"}, 409, "Restored 1, but not all", None),
    ],
)
def test_batch_restore_status_follows_the_database(api, outcomes, status, restored, refused):
    api.holder["client"], calls = _user_client(outcomes=outcomes)
    resp = api.post("/api/deployments/batch/restore", json={"deployment_ids": list(outcomes), "deleted_at": TS})
    assert resp.status_code == status
    assert all(c == ("restore_deployment", {"p_id": c[1]["p_id"], "p_deleted_at": TS}) for c in calls)
    if status == 403:
        assert "theirs" in resp.json()["detail"]
    elif status == 409:
        assert resp.json()["detail"] == f"{restored}: camera 'Gate cam' already has an open deployment. End that deployment first."
    else:
        assert resp.json() == {"restored": restored, "refused_ids": refused}


@pytest.mark.parametrize("deleted_at", ["2026-10-10T01:02:03.456789", "ts"])
def test_restore_refuses_a_naive_or_malformed_timestamp(api, deleted_at):
    api.holder["client"], calls = _user_client(outcomes={"mine": True})
    resp = api.post("/api/deployments/batch/restore", json={"deployment_ids": ["mine"], "deleted_at": deleted_at})
    assert resp.status_code == 422
    resp = api.post(f"/api/projects/{_PROJECT}/restore", json={"deleted_at": deleted_at})
    assert resp.status_code == 422
    assert calls == []


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


@pytest.mark.parametrize(
    ("visible", "outcome", "status"),
    [
        (True, None, 200),  # project_admin or ww_admin
        (True, "42501", 403),  # organisation manager who is not a project admin, member, viewer
        (False, None, 404),  # no role reaching the project
        (True, "P0002", 404),
    ],
)
def test_project_delete_status_follows_the_database(api, visible, outcome, status):
    api.holder["client"], calls = _user_client([_PROJECT] if visible else [], {_PROJECT: outcome})
    resp = api.delete(f"/api/projects/{_PROJECT}")
    assert resp.status_code == status
    if status == 200:
        assert resp.json() == {"id": _PROJECT, "deleted_at": calls[0][1]["p_deleted_at"]}


def test_project_delete_rejects_a_malformed_id(api):
    assert api.delete("/api/projects/not-a-uuid").status_code == 404
    assert api.post("/api/projects/not-a-uuid/restore", json={"deleted_at": TS}).status_code == 404


@pytest.mark.parametrize(
    ("outcome", "status", "restored"),
    [
        (True, 200, True),  # project_admin or ww_admin
        (False, 200, False),  # nothing deleted at that timestamp, e.g. an Undo that already ran
        ("42501", 403, None),  # anyone else, an organisation manager included
        ("P0002", 404, None),
        ("23P01", 409, None),  # a camera has another open deployment; nothing restored
    ],
)
def test_project_restore_status_follows_the_database(api, outcome, status, restored):
    api.holder["client"], calls = _user_client(outcomes={_PROJECT: outcome})
    resp = api.post(f"/api/projects/{_PROJECT}/restore", json={"deleted_at": TS})
    assert resp.status_code == status
    assert calls == [("restore_project", {"p_id": _PROJECT, "p_deleted_at": TS})]
    if status == 200:
        assert resp.json() == {"id": _PROJECT, "restored": restored}
    if status == 409:
        assert resp.json()["detail"] == "Not restored: camera 'Gate cam' already has an open deployment. End that deployment first."


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
    return api, api.holder


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
