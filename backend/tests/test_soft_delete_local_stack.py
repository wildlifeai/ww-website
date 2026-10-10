# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Deployment and project delete and Undo against the database's own functions (#349): a restore
brings back exactly the rows its delete removed, and nothing deleted earlier.

``test_soft_delete.py`` maps the functions' answers on a fake; this proves the timestamp the API
hands out round-trips through PostgREST and Postgres, and that the routes never reach for the
service role. Runs only when pointed at a LOCAL stack built from ww-backend ``dev``. It creates
an organisation, a project, two cameras, up to five deployments with photos and observations, and
three signed-in users (a project admin, a project member and an outsider), and deletes them all
afterwards. From a ww-backend checkout:

    eval "$(npx supabase status -o env | sed -n -E 's/^(API_URL|ANON_KEY|SERVICE_ROLE_KEY)=/export WW_TEST_\\1=/p')"
    cd <ww-website>/backend && pytest tests/test_soft_delete_local_stack.py

Without those variables the test is skipped, so ``pytest`` stays offline.
"""

import os
import re
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

URL = os.environ.get("WW_TEST_API_URL")
ANON_KEY = os.environ.get("WW_TEST_ANON_KEY")
SERVICE_KEY = os.environ.get("WW_TEST_SERVICE_ROLE_KEY")

pytestmark = pytest.mark.skipif(not (URL and ANON_KEY and SERVICE_KEY), reason="needs WW_TEST_API_URL, WW_TEST_ANON_KEY and WW_TEST_SERVICE_ROLE_KEY")

if URL and not re.match(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?/?$", URL):
    raise RuntimeError(f"WW_TEST_API_URL must be a local stack, got {URL}")

EARLIER = "2026-01-01T00:00:00+00:00"


def _no_service_client():
    raise AssertionError("delete and restore must not use the service role")


@pytest.fixture
def stack(monkeypatch):
    from fastapi.testclient import TestClient
    from supabase import create_client

    from app.config import settings
    from app.main import app
    from app.services import supabase_client

    url = URL.rstrip("/") + "/"
    svc = create_client(url, SERVICE_KEY)
    monkeypatch.setattr(settings, "SUPABASE_URL", url)
    monkeypatch.setattr(settings, "SUPABASE_ANON_KEY", ANON_KEY)
    # The routes never use the service role; the domain reads with it only to name a camera (409).
    monkeypatch.setattr(supabase_client, "_service_client", svc)
    monkeypatch.setattr("app.routers.deployments.create_service_client", _no_service_client)
    monkeypatch.setattr("app.routers.projects.create_service_client", _no_service_client)

    run = uuid.uuid4().hex[:10]
    password = uuid.uuid4().hex  # throwaway local users; nothing is stored
    users: dict[str, str] = {}
    tokens: dict[str, str] = {}
    org_id = project_id = None
    device_ids: list[str] = []
    try:
        org_id = svc.table("organisations").insert({"name": f"Soft delete test {run}", "slug": f"soft-delete-test-{run}"}).execute().data[0]["id"]
        project_id = svc.table("projects").insert({"name": f"Soft delete {run}", "organisation_id": org_id}).execute().data[0]["id"]
        # The second camera is for the one-open-deployment-per-camera case.
        for n, name in enumerate((f"Cam {run}", f"Gate cam {run}")):
            row = {"name": name, "bluetooth_id": f"bt-sd-{run}-{n}", "organisation_id": org_id}
            device_ids.append(svc.table("devices").insert(row).execute().data[0]["id"])
        for who, role in (("admin", "project_admin"), ("member", "project_member"), ("outsider", None)):
            email = f"softdelete-{who}-{run}@example.test"
            created = svc.auth.admin.create_user(
                {"email": email, "password": password, "email_confirm": True, "user_metadata": {"name": f"{who} {run}"}}
            )
            users[who] = created.user.id
            if role:
                svc.table("user_roles").insert({"user_id": created.user.id, "role": role, "scope_type": "project", "scope_id": project_id}).execute()
            session = create_client(url, ANON_KEY).auth.sign_in_with_password({"email": email, "password": password}).session
            tokens[who] = session.access_token

        def deployment(name, day, setup_by, deleted_at=None):
            row = {
                "name": name,
                "location_name": "Here",
                "deployment_start": f"2026-09-0{day}T00:00:00Z",
                "deployment_end": f"2026-09-0{day}T12:00:00Z",
                "project_id": project_id,
                "device_id": device_ids[0],
                "setup_by": setup_by,
                "deleted_at": deleted_at,
            }
            dep = svc.table("deployments").insert(row).execute().data[0]["id"]
            # Two live photos with an observation each, and one photo deleted earlier.
            for i, media_deleted in enumerate((None, None, EARLIER)):
                media = (
                    svc.table("media")
                    .insert({"deployment_id": dep, "file_path": f"gdrive://sd-{run}-{name}-{i}", "deleted_at": media_deleted or deleted_at})
                    .execute()
                    .data[0]["id"]
                )
                svc.table("observations").insert(
                    {
                        "deployment_id": dep,
                        "media_id": media,
                        "observation_type": "animal",
                        "source_type": "ai",
                        "deleted_at": media_deleted or deleted_at,
                    }
                ).execute()
            return dep

        deps = {
            "member's": deployment("members", 1, users["member"]),
            "admin's": deployment("admins", 2, users["admin"]),
            "earlier": deployment("earlier", 3, users["admin"], EARLIER),
        }
        gate = SimpleNamespace(id=device_ids[1], name=f"Gate cam {run}")
        yield SimpleNamespace(client=TestClient(app), svc=svc, project_id=project_id, deps=deps, tokens=tokens, users=users, gate=gate)
    finally:
        if project_id:
            dep_ids = [r["id"] for r in svc.table("deployments").select("id").eq("project_id", project_id).execute().data]
            if dep_ids:
                svc.table("observations").delete().in_("deployment_id", dep_ids).execute()
                svc.table("media").delete().in_("deployment_id", dep_ids).execute()
                svc.table("deployments").delete().in_("id", dep_ids).execute()
            svc.table("projects").delete().eq("id", project_id).execute()
        if device_ids:
            svc.table("devices").delete().in_("id", device_ids).execute()
        if org_id:
            svc.table("organisations").delete().eq("id", org_id).execute()
        for user_id in users.values():
            svc.table("user_roles").delete().eq("user_id", user_id).execute()
            svc.auth.admin.delete_user(user_id)


def _state(stack) -> dict[str, str | None]:
    """``deleted_at`` of the project and every row under it, by table and id."""
    svc = stack.svc
    state = {f"projects/{stack.project_id}": svc.table("projects").select("deleted_at").eq("id", stack.project_id).execute().data[0]["deleted_at"]}
    deps = svc.table("deployments").select("id, deleted_at").eq("project_id", stack.project_id).execute().data
    dep_ids = [d["id"] for d in deps]
    state.update({f"deployments/{d['id']}": d["deleted_at"] for d in deps})
    for table in ("media", "observations"):
        state.update(
            {f"{table}/{r['id']}": r["deleted_at"] for r in svc.table(table).select("id, deleted_at").in_("deployment_id", dep_ids).execute().data}
        )
    return state


def _same_instant(a: str | None, b: str) -> bool:
    return a is not None and datetime.fromisoformat(a) == datetime.fromisoformat(b)


def _as(stack, who: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {stack.tokens[who]}"}


def test_deployment_delete_and_undo_round_trip(stack):
    client, mine, theirs = stack.client, stack.deps["member's"], stack.deps["admin's"]
    before = _state(stack)

    resp = client.request("DELETE", "/api/deployments/batch", json={"deployment_ids": [mine, theirs]}, headers=_as(stack, "member"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deployment_ids"] == [mine] and body["refused_ids"] == [theirs]
    ts = body["deleted_at"]

    after = _state(stack)
    changed = {k for k in before if before[k] != after[k]}
    # Exactly the member's deployment and its live photos and observations, all at ts.
    assert changed and all(_same_instant(after[k], ts) for k in changed)
    assert f"deployments/{mine}" in changed and not any(before[k] for k in changed)
    assert len(changed) == 1 + 2 + 2

    # An outsider cannot undo it; the admin's deployment is not the member's to restore.
    refused = client.post("/api/deployments/batch/restore", json={"deployment_ids": [mine], "deleted_at": ts}, headers=_as(stack, "outsider"))
    assert refused.status_code == 403
    assert _state(stack) == after

    undo = client.post("/api/deployments/batch/restore", json={"deployment_ids": [mine, theirs], "deleted_at": ts}, headers=_as(stack, "member"))
    assert undo.status_code == 200, undo.text
    assert undo.json() == {"restored": 1, "refused_ids": [theirs]}
    assert _state(stack) == before


def test_project_delete_and_undo_round_trip(stack):
    client, pid = stack.client, stack.project_id
    before = _state(stack)

    assert client.delete(f"/api/projects/{pid}", headers=_as(stack, "member")).status_code == 403
    resp = client.delete(f"/api/projects/{pid}", headers=_as(stack, "admin"))
    assert resp.status_code == 200, resp.text
    ts = resp.json()["deleted_at"]

    after = _state(stack)
    changed = {k for k in before if before[k] != after[k]}
    # Everything that was live, at ts; the deployment and photos deleted earlier keep their stamp.
    assert changed == {k for k, v in before.items() if v is None}
    assert all(_same_instant(after[k], ts) for k in changed)

    # A member cannot undo it, and a timestamp nothing was deleted at restores nothing.
    assert client.post(f"/api/projects/{pid}/restore", json={"deleted_at": ts}, headers=_as(stack, "member")).status_code == 403
    other = client.post(f"/api/projects/{pid}/restore", json={"deleted_at": "2000-01-01T00:00:00Z"}, headers=_as(stack, "admin"))
    assert other.status_code == 200 and other.json() == {"id": pid, "restored": False}
    assert _state(stack) == after

    undo = client.post(f"/api/projects/{pid}/restore", json={"deleted_at": ts}, headers=_as(stack, "admin"))
    assert undo.status_code == 200, undo.text
    assert undo.json() == {"id": pid, "restored": True}
    assert _state(stack) == before


def _open_deployment(stack, name: str, setup_by: str) -> str:
    """An open deployment (no end) on the gate camera, written with the service role."""
    row = {
        "name": name,
        "location_name": "Gate",
        "deployment_start": {"first": "2026-10-01", "second": "2026-10-05", "third": "2026-10-08"}[name] + "T00:00:00Z",
        "project_id": stack.project_id,
        "device_id": stack.gate.id,
        "setup_by": setup_by,
    }
    return stack.svc.table("deployments").insert(row).execute().data[0]["id"]


def test_undo_is_refused_while_the_camera_has_another_open_deployment(stack):
    """One open deployment per camera (ww-backend #320): an Undo that would give the camera a
    second one is a 409 naming it, and changes nothing."""
    client, pid, member = stack.client, stack.project_id, stack.users["member"]
    conflict = f"camera '{stack.gate.name}' already has an open deployment. End that deployment first."

    first = _open_deployment(stack, "first", member)
    resp = client.request("DELETE", "/api/deployments/batch", json={"deployment_ids": [first]}, headers=_as(stack, "member"))
    assert resp.status_code == 200, resp.text
    ts = resp.json()["deleted_at"]
    _open_deployment(stack, "second", member)  # allowed: the first is deleted

    before = _state(stack)
    undo = client.post("/api/deployments/batch/restore", json={"deployment_ids": [first], "deleted_at": ts}, headers=_as(stack, "member"))
    assert undo.status_code == 409, undo.text
    assert undo.json()["detail"] == f"Nothing restored: {conflict}"
    assert _state(stack) == before

    # The project's Undo is one transaction: the clash rolls all of it back.
    resp = client.delete(f"/api/projects/{pid}", headers=_as(stack, "admin"))
    assert resp.status_code == 200, resp.text
    project_ts = resp.json()["deleted_at"]
    _open_deployment(stack, "third", member)  # the second is deleted with the project
    before = _state(stack)
    undo = client.post(f"/api/projects/{pid}/restore", json={"deleted_at": project_ts}, headers=_as(stack, "admin"))
    assert undo.status_code == 409, undo.text
    assert undo.json()["detail"] == f"Not restored: {conflict}"
    assert _state(stack) == before
