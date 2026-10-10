# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for security remediations: job-status authorization + media SSRF guard."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.domain.media_resolver import _is_safe_public_url
from app.routers.jobs import _authorize_job

# ── Job status: owner-scoped polling (was unauthenticated IDOR) ───────────────


def test_authorize_job_owner_ok():
    _authorize_job(SimpleNamespace(user_id="u1"), SimpleNamespace(id="u1"))  # no raise


def test_authorize_job_other_user_hidden_as_404():
    with pytest.raises(HTTPException) as exc:
        _authorize_job(SimpleNamespace(user_id="u1"), SimpleNamespace(id="u2"))
    assert exc.value.status_code == 404  # 404, not 403 — don't confirm the id exists


def test_authorize_job_ownerless_allowed():
    # Legacy and system jobs carry no owner, so any signed-in user may read them.
    _authorize_job(SimpleNamespace(user_id=None, organisation_id=None), SimpleNamespace(id="anyone"))  # no raise


def test_authorize_job_api_key_job_hidden_as_404():
    # An organisation API key's export: its link is read with that organisation's keys only (#327).
    with pytest.raises(HTTPException) as exc:
        _authorize_job(SimpleNamespace(user_id=None, organisation_id="org-1"), SimpleNamespace(id="anyone"))
    assert exc.value.status_code == 404


# ── Media resolver SSRF guard ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x.jpg",  # loopback
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata (link-local)
        "http://10.0.0.5/x.jpg",  # private
        "http://192.168.1.1/x.jpg",  # private
        "http://0.0.0.0/x.jpg",  # unspecified
        "ftp://example.com/x.jpg",  # non-http scheme
        "file:///etc/passwd",  # file scheme
        "not-a-url",  # unparseable / no host
    ],
)
async def test_ssrf_guard_blocks_unsafe(url):
    assert await _is_safe_public_url(url) is False


async def test_ssrf_guard_allows_public_ip():
    # Literal public IP — no network DNS needed, deterministic.
    assert await _is_safe_public_url("https://8.8.8.8/image.jpg") is True


# ── Object-level authorization decision (pure) ────────────────────────────────


def test_authz_system_admin_sees_everything():
    from app.authz import _has_access

    roles = [{"scope_type": "system", "scope_id": None, "role": "ww_admin"}]
    assert _has_access(roles, "org-x", "proj-x") is True


def test_authz_org_member_access_own_org_only():
    from app.authz import _has_access

    roles = [{"scope_type": "organisation", "scope_id": "org-1", "role": "organisation_member"}]
    assert _has_access(roles, "org-1", "proj-9") is True  # resource in their org
    assert _has_access(roles, "org-2", "proj-9") is False  # another org


def test_authz_project_role_scoped_to_project():
    from app.authz import _has_access

    roles = [{"scope_type": "project", "scope_id": "proj-1", "role": "project_member"}]
    assert _has_access(roles, "org-1", "proj-1") is True
    assert _has_access(roles, "org-1", "proj-2") is False


def test_authz_no_roles_denied():
    from app.authz import _has_access

    assert _has_access([], "org-1", "proj-1") is False


def test_authz_expired_role_ignored():
    from app.authz import _has_access

    roles = [{"scope_type": "organisation", "scope_id": "org-1", "expires_at": "2000-01-01T00:00:00+00:00"}]
    assert _has_access(roles, "org-1", None) is False
