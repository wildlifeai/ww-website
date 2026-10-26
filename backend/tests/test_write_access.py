# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The write-level access check (UI audit decision 1: a project_viewer is read-only everywhere).

``_can_write`` mirrors ww-backend's ``has_project_role(user, project, 'project_member')``; the
routes that write with the service role, or start work, call it through ``assert_write`` and
the ``require_*_write`` dependencies. The read check, ``_has_access``, is pinned in
``test_security_fixes.py``.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import authz
from app.authz import WRITE_REFUSED, _can_write, _project_creator_org_ids
from tests.fake_postgrest import FakeDB

ORG = "org-1"


def _role(role, scope_type, scope_id, **extra):
    return {"role": role, "scope_type": scope_type, "scope_id": scope_id, **extra}


# ── _can_write (pure) ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        (_role("project_admin", "project", "p1"), True),
        (_role("project_member", "project", "p1"), True),
        (_role("project_viewer", "project", "p1"), False),
        (_role("ww_admin", "system", None), True),
        (_role("organisation_manager", "organisation", ORG), False),
        (_role("organisation_member", "organisation", ORG), False),
    ],
)
def test_can_write_follows_has_project_role_member(role, allowed):
    assert _can_write([role], "p1") is allowed


def test_can_write_is_scoped_to_the_project():
    assert _can_write([_role("project_member", "project", "p1")], "p2") is False
    assert _can_write([_role("project_member", "project", "p1")], None) is False


def test_can_write_ignores_an_expired_role():
    expired = _role("project_member", "project", "p1", expires_at="2000-01-01T00:00:00+00:00")
    later = _role("project_member", "project", "p1", expires_at="2999-01-01T00:00:00Z")
    assert _can_write([expired], "p1") is False
    assert _can_write([later], "p1") is True


def test_can_write_needs_ww_admin_at_system_scope():
    """Only ww_admin holds system scope today (user_roles constraint); the check names both."""
    assert _can_write([_role("ww_admin", "organisation", ORG)], "p1") is False


def test_project_creator_orgs_are_active_organisation_roles():
    roles = [
        _role("organisation_member", "organisation", "o1"),
        _role("organisation_manager", "organisation", "o2"),
        _role("organisation_member", "organisation", "o3", expires_at="2000-01-01T00:00:00Z"),
        _role("project_admin", "project", "p1"),
        _role("ww_admin", "system", None),
    ]
    assert _project_creator_org_ids(roles) == ["o1", "o2"]


# ── assert_write, writable_media_ids, classify_deployment_access ─────


def _db(roles):
    return FakeDB(
        {
            "projects": [{"id": "p1", "organisation_id": ORG}, {"id": "p2", "organisation_id": ORG}],
            "deployments": [{"id": "d1", "project_id": "p1"}, {"id": "d2", "project_id": "p2"}],
            "media": [{"id": "m1", "deployment_id": "d1"}, {"id": "m2", "deployment_id": "d2"}],
            "user_roles": [{"user_id": "u1", "is_active": True, **r} for r in roles],
        }
    )


async def test_assert_write_allows_a_member(monkeypatch):
    monkeypatch.setattr(authz, "create_service_client", _db([_role("project_member", "project", "p1")]).client)
    await authz.assert_write("u1", deployment_id="d1")
    await authz.assert_write("u1", media_id="m1")
    await authz.assert_write("u1", project_id="p1")


async def test_assert_write_refuses_a_viewer_with_403(monkeypatch):
    monkeypatch.setattr(authz, "create_service_client", _db([_role("project_viewer", "project", "p1")]).client)
    with pytest.raises(HTTPException) as exc:
        await authz.assert_write("u1", deployment_id="d1")
    assert exc.value.status_code == 403
    assert exc.value.detail == WRITE_REFUSED


async def test_assert_write_refuses_an_organisation_manager_with_403(monkeypatch):
    monkeypatch.setattr(authz, "create_service_client", _db([_role("organisation_manager", "organisation", ORG)]).client)
    with pytest.raises(HTTPException) as exc:
        await authz.assert_write("u1", project_id="p2")
    assert exc.value.status_code == 403


async def test_assert_write_hides_what_the_caller_cannot_read(monkeypatch):
    """No role reaching the project, or no such resource, stays a 404 so ids can't be probed."""
    monkeypatch.setattr(authz, "create_service_client", _db([_role("project_member", "project", "p1")]).client)
    for resource in ({"deployment_id": "d2"}, {"deployment_id": "nope"}):
        with pytest.raises(HTTPException) as exc:
            await authz.assert_write("u1", **resource)
        assert exc.value.status_code == 404


async def test_writable_media_ids_keeps_the_callers_projects_in_order(monkeypatch):
    roles = [_role("project_member", "project", "p1"), _role("project_viewer", "project", "p2")]
    monkeypatch.setattr(authz, "create_service_client", _db(roles).client)
    assert await authz.writable_media_ids("u1", ["m2", "m1", "nope", "m1"]) == ["m1", "m1"]
    assert await authz.writable_media_ids("u1", []) == []


async def test_classify_deployment_access_gives_a_viewer_no_access(monkeypatch):
    roles = [_role("project_member", "project", "p1"), _role("project_viewer", "project", "p2")]
    monkeypatch.setattr(authz, "create_service_client", _db(roles).client)
    result = await authz.classify_deployment_access("u1", ["d1", "d2", "d9"])
    assert result == {"d1": "valid", "d2": "no_access", "d9": "not_found"}


# ── Routes ───────────────────────────────────────────────────────────


async def test_inat_publish_refuses_when_no_photo_is_writable(monkeypatch):
    from app.routers import inaturalist
    from app.schemas.inaturalist import INatPublishRequest

    async def none_writable(_user_id, _ids):
        return []

    published = []

    async def publish(**kwargs):
        published.append(kwargs)
        return {}

    monkeypatch.setattr(inaturalist.settings, "FF_INAT_ENABLED", True)
    monkeypatch.setattr(inaturalist.settings, "INAT_CLIENT_SECRET", "x" * 43)
    monkeypatch.setattr(inaturalist, "writable_media_ids", none_writable)
    monkeypatch.setattr(inaturalist, "publish_media_to_inat", publish)
    with pytest.raises(HTTPException) as exc:
        await inaturalist.publish_to_inat(INatPublishRequest(media_ids=["m1"]), SimpleNamespace(state=SimpleNamespace()), SimpleNamespace(id="u1"))
    assert exc.value.status_code == 403
    assert not published


async def test_inat_publish_sends_only_the_writable_photos(monkeypatch):
    from app.routers import inaturalist
    from app.schemas.inaturalist import INatPublishRequest

    async def first_only(_user_id, ids):
        return ids[:1]

    published = []

    async def publish(**kwargs):
        published.append(kwargs["media_ids"])
        return {}

    monkeypatch.setattr(inaturalist.settings, "FF_INAT_ENABLED", True)
    monkeypatch.setattr(inaturalist.settings, "INAT_CLIENT_SECRET", "x" * 43)
    monkeypatch.setattr(inaturalist, "writable_media_ids", first_only)
    monkeypatch.setattr(inaturalist, "publish_media_to_inat", publish)
    await inaturalist.publish_to_inat(INatPublishRequest(media_ids=["m1", "m2"]), SimpleNamespace(state=SimpleNamespace()), SimpleNamespace(id="u1"))
    assert published == [["m1"]]
