# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""API key management (#307): who may create, list and revoke keys, and what the service writes
to ww-backend's ``api_keys`` table."""

import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.authz import _is_org_manager
from app.services import api_key

# The columns of public.api_keys (ww-backend migration 20261009203923_api_keys_table.sql).
API_KEYS_COLUMNS = {
    "id",
    "organisation_id",
    "created_by",
    "name",
    "key_hash",
    "key_prefix",
    "scopes",
    "expires_at",
    "last_used_at",
    "revoked_at",
    "created_at",
}

ORG = "11111111-1111-1111-1111-111111111111"
OTHER_ORG = "22222222-2222-2222-2222-222222222222"
KEY_ID = "33333333-3333-3333-3333-333333333333"


def _role(role, scope_type, scope_id, expires_at=None):
    return {"role": role, "scope_type": scope_type, "scope_id": scope_id, "expires_at": expires_at}


_PAST = "2020-01-01T00:00:00Z"
_MANAGER = _role("organisation_manager", "organisation", ORG)


# ── The rule ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("roles", "allowed"),
    [
        ([_MANAGER], True),
        ([_role("organisation_manager", "organisation", OTHER_ORG)], False),
        ([_role("organisation_member", "organisation", ORG)], False),
        ([_role("organisation_manager", "system", None)], False),
        ([_role("ww_admin", "system", None)], False),
        ([_role("project_admin", "project", "proj-1")], False),
        ([_role("organisation_manager", "organisation", ORG, expires_at=_PAST)], False),
        ([], False),
    ],
)
def test_only_the_organisations_own_manager_may_manage_keys(roles, allowed):
    assert _is_org_manager(roles, ORG) is allowed


# ── Routes ───────────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, data):
        self.data = data


def _chain(rows):
    t = MagicMock()
    for m in ("select", "eq", "in_", "is_", "limit"):
        getattr(t, m).return_value = t
    t.execute.return_value = _Result(rows)
    return t


@pytest.fixture
def api(monkeypatch):
    """A signed-in caller whose roles each test sets, the flag on, and the key service recorded."""
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.dependencies import get_current_user
    from app.main import app

    user = SimpleNamespace(id="u1", app_metadata={}, email_confirmed_at="2026-01-01T00:00:00Z")
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(settings, "FF_PUBLIC_API_ENABLED", True)

    state = SimpleNamespace(roles=[], calls=[], revoked=True)

    def service_client():
        client = MagicMock()
        client.table.side_effect = lambda name: _chain(state.roles if name == "user_roles" else [])
        return client

    async def create(**kwargs):
        state.calls.append(("create", kwargs))
        return "ww_live_" + "a" * 32, {
            "id": KEY_ID,
            "name": kwargs["name"],
            "key_prefix": "ww_live_aaaaaaaa",
            "scopes": kwargs["scopes"],
            "expires_at": None,
            "created_at": "2026-10-10T00:00:00+00:00",
        }

    async def list_keys(org_id):
        state.calls.append(("list", org_id))
        return [
            {"id": KEY_ID, "name": "sync", "key_prefix": "ww_live_aaaaaaaa", "scopes": ["devices:read"], "created_at": "2026-10-10T00:00:00+00:00"}
        ]

    async def revoke(key_id, org_id):
        state.calls.append(("revoke", key_id, org_id))
        return state.revoked

    monkeypatch.setattr("app.authz.create_service_client", service_client)
    monkeypatch.setattr("app.routers.public_api.create_api_key_record", create)
    monkeypatch.setattr("app.routers.public_api.list_api_keys", list_keys)
    monkeypatch.setattr("app.routers.public_api.revoke_api_key", revoke)
    yield TestClient(app), state
    app.dependency_overrides.clear()


def _create(client, org=ORG, **body):
    return client.post("/api/v1/api-keys", json={"organisation_id": org, "name": "sync", "scopes": ["devices:read"], **body})


def _list(client, org=ORG):
    return client.get("/api/v1/api-keys", params={"organisation_id": org})


def _revoke(client, org=ORG):
    return client.delete(f"/api/v1/api-keys/{KEY_ID}", params={"organisation_id": org})


def test_manager_creates_lists_and_revokes(api):
    client, state = api
    state.roles = [_MANAGER]

    created = _create(client)
    assert created.status_code == 200
    assert created.json()["data"]["key"] == "ww_live_" + "a" * 32
    assert _list(client).json()["data"][0]["key_prefix"] == "ww_live_aaaaaaaa"
    assert _revoke(client).json()["data"] == {"revoked": True}

    assert [c[0] for c in state.calls] == ["create", "list", "revoke"]
    assert state.calls[0][1]["org_id"] == ORG and state.calls[0][1]["user_id"] == "u1"
    assert state.calls[1] == ("list", ORG)
    assert state.calls[2] == ("revoke", KEY_ID, ORG)


# 403 for a caller with a role reaching the organisation, 404 for one with none, so another
# tenant's organisation cannot be probed. Nothing reaches the key service either way.
@pytest.mark.parametrize(
    ("roles", "status"),
    [
        ([_role("organisation_member", "organisation", ORG)], 403),
        ([_role("ww_admin", "system", None)], 403),
        ([_role("organisation_manager", "organisation", ORG, expires_at=_PAST)], 404),
        ([_role("organisation_manager", "organisation", OTHER_ORG)], 404),
        ([_role("project_admin", "project", "proj-1")], 404),
        ([], 404),
    ],
)
@pytest.mark.parametrize("call", [_create, _list, _revoke])
def test_everyone_else_is_refused(api, roles, status, call):
    client, state = api
    state.roles = roles
    assert call(client).status_code == status
    assert state.calls == []


def test_revoking_a_key_the_organisation_does_not_have_is_404(api):
    client, state = api
    state.roles = [_MANAGER]
    state.revoked = False
    assert _revoke(client).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"scopes": []},
        {"name": "   "},
        {"name": "x" * 101},
        {"expires_at": "next week"},
    ],
)
def test_create_rejects_a_bad_body(api, body):
    client, state = api
    state.roles = [_MANAGER]
    assert _create(client, **body).status_code == 422
    assert state.calls == []


def test_organisation_must_be_named_and_a_uuid(api):
    client, state = api
    state.roles = [_MANAGER]
    assert client.get("/api/v1/api-keys").status_code == 422
    assert _list(client, org="not-a-uuid").status_code == 422
    assert client.delete("/api/v1/api-keys/not-a-uuid", params={"organisation_id": ORG}).status_code == 422
    assert state.calls == []


def test_demo_account_cannot_create_or_revoke(api):
    from app.dependencies import get_current_user
    from app.main import app

    client, state = api
    state.roles = [_MANAGER]
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u1", app_metadata={"is_demo": True})
    assert _create(client).status_code == 403
    assert _revoke(client).status_code == 403
    assert state.calls == []


def test_flag_off_answers_feature_disabled(api, monkeypatch):
    from app.config import settings

    client, state = api
    state.roles = [_MANAGER]
    monkeypatch.setattr(settings, "FF_PUBLIC_API_ENABLED", False)
    for call in (_create, _list, _revoke):
        assert call(client).json()["error"]["code"] == "FEATURE_DISABLED"
    assert state.calls == []


# ── The service against the table's columns ──────────────────────────────────


class _Recorder:
    """A stand-in for ``client.table("api_keys")`` that records what the service sends."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.selects: list[str] = []
        self.inserted: list[dict] = []
        self.updated: list[dict] = []
        self.ranges: list[tuple[int, int]] = []

    def select(self, cols):
        self.selects.append(cols)
        return self

    def insert(self, row):
        self.inserted.append(row)
        return self

    def update(self, row):
        self.updated.append(row)
        return self

    def range(self, lo, hi):
        self.ranges.append((lo, hi))
        return self

    def eq(self, *a):
        return self

    def is_(self, *a):
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        return _Result(self.pages.pop(0) if self.pages else [])


@pytest.fixture
def table(monkeypatch):
    def install(*pages):
        rec = _Recorder(pages)
        client = MagicMock()
        client.table.side_effect = lambda name: rec if name == "api_keys" else pytest.fail(f"unexpected table {name}")
        monkeypatch.setattr(api_key, "create_service_client", lambda: client)
        return rec

    return install


def _columns(select: str) -> set[str]:
    return {c.strip() for c in select.split(",")}


async def test_create_writes_only_the_tables_columns(table):
    expires = datetime.now(timezone.utc) + timedelta(days=30)
    rec = table([{"id": KEY_ID, "key_prefix": "x"}])

    raw, _ = await api_key.create_api_key_record(ORG, "u1", "sync", ["devices:read", "devices:read"], expires_at=expires)

    row = rec.inserted[0]
    assert set(row) <= API_KEYS_COLUMNS
    assert row["key_hash"] == hashlib.sha256(raw.encode()).hexdigest()
    assert raw.startswith("ww_live_") and len(raw) == 8 + 32
    assert row["key_prefix"] == raw[:16]
    assert row["scopes"] == ["devices:read"]
    assert datetime.fromisoformat(row["expires_at"]) == expires


async def test_create_reads_a_naive_expiry_as_utc(table):
    rec = table([{"id": KEY_ID}])
    naive = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
    await api_key.create_api_key_record(ORG, "u1", "sync", ["devices:read"], expires_at=naive)
    assert datetime.fromisoformat(rec.inserted[0]["expires_at"]) == naive.replace(tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("scopes", "expires_at"),
    [
        ([], None),
        (["devices:write"], None),
        (["devices:read"], datetime.now(timezone.utc) - timedelta(seconds=1)),
    ],
)
async def test_create_refuses_bad_scopes_and_past_expiry(table, scopes, expires_at):
    rec = table()
    with pytest.raises(api_key.ApiKeyError):
        await api_key.create_api_key_record(ORG, "u1", "sync", scopes, expires_at=expires_at)
    assert rec.inserted == []


async def test_validate_reads_existing_columns_and_stamps_last_used(table):
    raw, _ = api_key.generate_api_key()
    rec = table([{"id": KEY_ID, "organisation_id": ORG, "scopes": ["devices:read"], "expires_at": None}])

    key = await api_key.validate_api_key(raw, "devices:read")

    assert key["organisation_id"] == ORG
    assert _columns(rec.selects[0]) <= API_KEYS_COLUMNS
    assert rec.updated == [{"last_used_at": "now()"}]


@pytest.mark.parametrize(
    ("rows", "scope"),
    [
        ([], None),  # unknown or revoked
        ([{"id": KEY_ID, "organisation_id": ORG, "scopes": ["devices:read"], "expires_at": _PAST}], None),
        ([{"id": KEY_ID, "organisation_id": ORG, "scopes": ["devices:read"], "expires_at": None}], "telemetry:read"),
    ],
)
async def test_validate_refuses_unknown_expired_and_out_of_scope_keys(table, rows, scope):
    raw, _ = api_key.generate_api_key()
    rec = table(rows)
    with pytest.raises(api_key.ApiKeyError):
        await api_key.validate_api_key(raw, scope)
    assert rec.updated == []


async def test_validate_refuses_another_format_without_a_lookup(table):
    rec = table()
    with pytest.raises(api_key.ApiKeyError):
        await api_key.validate_api_key("sk_live_abc")
    assert rec.selects == []


async def test_list_reads_existing_columns_and_pages_past_the_row_cap(table):
    first = [{"id": str(i)} for i in range(1000)]
    rec = table(first, [{"id": "last"}])

    rows = await api_key.list_api_keys(ORG)

    assert len(rows) == 1001
    assert rec.ranges == [(0, 999), (1000, 1999)]
    assert _columns(rec.selects[0]) <= API_KEYS_COLUMNS
    assert "key_hash" not in _columns(rec.selects[0])
