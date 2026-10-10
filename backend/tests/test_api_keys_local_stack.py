# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""API keys against a real ``api_keys`` table (#307): create, use and revoke a key through the
routes, as an organisation manager, with an organisation member refused.

Runs only when pointed at a LOCAL stack built from ww-backend ``dev`` (it creates an
organisation and two users with the service role key, and deletes them afterwards). From a
ww-backend checkout:

    eval "$(npx supabase status -o env | sed -n -E 's/^(API_URL|SERVICE_ROLE_KEY)=/export WW_TEST_\\1=/p')"
    cd <ww-website>/backend && pytest tests/test_api_keys_local_stack.py

Without those variables the test is skipped, so ``pytest`` stays offline.
"""

import os
import re
import uuid
from types import SimpleNamespace

import pytest

URL = os.environ.get("WW_TEST_API_URL")
SERVICE_KEY = os.environ.get("WW_TEST_SERVICE_ROLE_KEY")

pytestmark = pytest.mark.skipif(not (URL and SERVICE_KEY), reason="needs WW_TEST_API_URL and WW_TEST_SERVICE_ROLE_KEY")

if URL and not re.match(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?/?$", URL):
    raise RuntimeError(f"WW_TEST_API_URL must be a local stack, got {URL}")


@pytest.fixture
def stack(monkeypatch):
    """An organisation with a manager and a member, the app's service client on the local stack."""
    from fastapi.testclient import TestClient
    from supabase import create_client

    from app.config import settings
    from app.dependencies import get_current_user
    from app.main import app
    from app.services import supabase_client

    svc = create_client(URL.rstrip("/") + "/", SERVICE_KEY)
    monkeypatch.setattr(supabase_client, "_service_client", svc)
    monkeypatch.setattr(settings, "FF_PUBLIC_API_ENABLED", True)

    run = uuid.uuid4().hex[:10]
    org_id = svc.table("organisations").insert({"name": f"API keys test {run}", "slug": f"api-keys-test-{run}"}).execute().data[0]["id"]
    users = {}
    try:
        for role in ("organisation_manager", "organisation_member"):
            created = svc.auth.admin.create_user(
                {"email": f"apikeys-{role}-{run}@example.test", "email_confirm": True, "user_metadata": {"name": f"{role} {run}"}}
            )
            users[role] = created.user.id
            svc.table("user_roles").insert({"user_id": created.user.id, "role": role, "scope_type": "organisation", "scope_id": org_id}).execute()

        caller = SimpleNamespace(id=users["organisation_manager"], app_metadata={})
        app.dependency_overrides[get_current_user] = lambda: caller
        yield SimpleNamespace(client=TestClient(app), svc=svc, org_id=org_id, users=users, caller=caller)
    finally:
        app.dependency_overrides.clear()
        svc.table("organisations").delete().eq("id", org_id).execute()  # cascades to its api_keys
        for user_id in users.values():
            svc.table("user_roles").delete().eq("user_id", user_id).execute()
            svc.auth.admin.delete_user(user_id)


def test_create_use_and_revoke_a_key(stack):
    client, org = stack.client, stack.org_id

    created = client.post("/api/v1/api-keys", json={"organisation_id": org, "name": "sync", "scopes": ["devices:read"]})
    assert created.status_code == 200, created.text
    key = created.json()["data"]
    raw = key["key"]
    assert re.fullmatch(r"ww_live_[0-9a-f]{32}", raw) and key["key_prefix"] == raw[:16]

    listed = client.get("/api/v1/api-keys", params={"organisation_id": org}).json()["data"]
    assert [(k["id"], k["key_prefix"], k["last_used_at"]) for k in listed] == [(key["id"], raw[:16], None)]

    used = client.get("/api/v1/devices", headers={"X-API-Key": raw})
    assert used.status_code == 200, used.text
    assert used.json()["data"] == []
    # A scope the key does not carry is refused.
    assert client.get("/api/v1/observations", headers={"X-API-Key": raw}).status_code == 401
    # last_used_at is a real timestamp once the key has been used.
    row = stack.svc.table("api_keys").select("last_used_at, created_by").eq("id", key["id"]).execute().data[0]
    assert row["last_used_at"] and row["created_by"] == stack.caller.id

    # A member of the organisation can neither list nor revoke its keys.
    stack.caller.id = stack.users["organisation_member"]
    assert client.get("/api/v1/api-keys", params={"organisation_id": org}).status_code == 403
    assert client.delete(f"/api/v1/api-keys/{key['id']}", params={"organisation_id": org}).status_code == 403
    stack.caller.id = stack.users["organisation_manager"]

    revoked = client.delete(f"/api/v1/api-keys/{key['id']}", params={"organisation_id": org})
    assert revoked.status_code == 200, revoked.text
    assert client.get("/api/v1/devices", headers={"X-API-Key": raw}).status_code == 401
    assert client.get("/api/v1/api-keys", params={"organisation_id": org}).json()["data"] == []
    assert client.delete(f"/api/v1/api-keys/{key['id']}", params={"organisation_id": org}).status_code == 404
