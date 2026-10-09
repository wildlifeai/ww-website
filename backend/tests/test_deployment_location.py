# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Editing a deployment's location (ww-website#288): the write runs as the caller, a 0-row update
is a refusal, and the time zone follows the new coordinates."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from postgrest.exceptions import APIError

DEP_ID = "e10f7c43-1111-4222-8333-944455556666"


class _Result:
    def __init__(self, data):
        self.data = data


def _user_client(updated_rows, visible_rows=()):
    """A user client whose UPDATE answers ``updated_rows`` and whose SELECT answers ``visible_rows``."""
    client = MagicMock()
    update_chain, select_chain = MagicMock(), MagicMock()
    for chain, rows in ((update_chain, updated_rows), (select_chain, list(visible_rows))):
        chain.eq.return_value = chain
        chain.is_.return_value = chain
        chain.execute.return_value = _Result(rows)
    table = MagicMock()
    table.update.return_value = update_chain
    table.select.return_value = select_chain
    client.table.return_value = table
    return client, table


# ── domain ───────────────────────────────────────────────────────────────────


def test_location_update_recomputes_timezone(monkeypatch):
    monkeypatch.setattr("app.domain.deployment_location.resolve_timezone", lambda lat, lon: f"tz:{lat},{lon}")
    from app.domain.deployment_location import location_update

    out = location_update({"location_name": "Ridge", "latitude": -41.2, "longitude": 174.7})
    assert out == {"location_name": "Ridge", "latitude": -41.2, "longitude": 174.7, "timezone": "tz:-41.2,174.7"}


def test_location_update_without_coordinates_clears_timezone():
    from app.domain.deployment_location import location_update

    assert location_update({"location_name": "Ridge", "latitude": None, "longitude": None})["timezone"] is None


def test_apply_returns_the_stored_row():
    from app.domain.deployment_location import apply_location_update

    stored = {"id": DEP_ID, "location_name": "Ridge", "timezone": "Pacific/Auckland", "project_id": "p", "setup_by": "u"}
    client, table = _user_client([stored])
    outcome, row = apply_location_update(client, DEP_ID, {"location_name": "Ridge"})
    assert outcome == "updated"
    assert row["location_name"] == "Ridge" and row["timezone"] == "Pacific/Auckland"
    assert "project_id" not in row and "setup_by" not in row
    table.update.assert_called_once_with({"location_name": "Ridge"})


def test_apply_zero_rows_on_a_visible_deployment_is_forbidden():
    from app.domain.deployment_location import apply_location_update

    client, _ = _user_client([], visible_rows=[{"id": DEP_ID}])
    assert apply_location_update(client, DEP_ID, {"location_name": "x"}) == ("forbidden", None)


def test_apply_zero_rows_on_an_unseen_deployment_is_not_found():
    from app.domain.deployment_location import apply_location_update

    client, _ = _user_client([], visible_rows=[])
    assert apply_location_update(client, DEP_ID, {"location_name": "x"}) == ("not_found", None)


# ── PATCH /api/deployments/{id}/location ─────────────────────────────────────


@pytest.fixture
def api(monkeypatch):
    """A test client signed in as a non-demo user, with the user client swappable per test."""
    from fastapi.testclient import TestClient

    from app.dependencies import get_current_user, get_user_client
    from app.main import app

    monkeypatch.setattr("app.domain.deployment_location.resolve_timezone", lambda lat, lon: "Pacific/Auckland" if lat is not None else None)
    holder = {}
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="user-1", app_metadata={})
    app.dependency_overrides[get_user_client] = lambda: holder["client"]
    yield TestClient(app), holder
    app.dependency_overrides.clear()


BODY = {"location_name": " Ridge track ", "location_description": "", "latitude": -41.2, "longitude": 174.7, "altitude": 120, "accuracy": None}


def test_patch_writes_as_the_user_and_returns_the_row(api):
    client, holder = api
    stored = {"id": DEP_ID, "location_name": "Ridge track", "latitude": -41.2, "longitude": 174.7, "timezone": "Pacific/Auckland"}
    holder["client"], table = _user_client([stored])
    res = client.patch(f"/api/deployments/{DEP_ID}/location", json=BODY)
    assert res.status_code == 200
    assert res.json()["data"]["timezone"] == "Pacific/Auckland"
    sent = table.update.call_args.args[0]
    assert sent["location_name"] == "Ridge track"
    assert sent["location_description"] is None
    assert sent["timezone"] == "Pacific/Auckland"
    assert set(sent) == {"location_name", "location_description", "latitude", "longitude", "altitude", "accuracy", "timezone"}


def test_patch_refused_by_rls_is_403(api):
    client, holder = api
    holder["client"], _ = _user_client([], visible_rows=[{"id": DEP_ID}])
    res = client.patch(f"/api/deployments/{DEP_ID}/location", json=BODY)
    assert res.status_code == 403
    assert "admin" in res.json()["detail"]


def test_patch_unseen_deployment_is_404(api):
    client, holder = api
    holder["client"], _ = _user_client([], visible_rows=[])
    assert client.patch(f"/api/deployments/{DEP_ID}/location", json=BODY).status_code == 404


def test_patch_bad_id_is_404(api):
    client, holder = api
    holder["client"], table = _user_client([])
    assert client.patch("/api/deployments/not-a-uuid/location", json=BODY).status_code == 404
    table.update.assert_not_called()


def test_patch_check_violation_is_422(api):
    client, holder = api
    holder["client"], table = _user_client([])
    table.update.return_value.execute.side_effect = APIError({"code": "23514", "message": "violates check"})
    assert client.patch(f"/api/deployments/{DEP_ID}/location", json=BODY).status_code == 422


@pytest.mark.parametrize(
    "change",
    [
        {"location_name": "   "},
        {"latitude": 91},
        {"longitude": -181},
        {"accuracy": -1},
        {"latitude": None},
    ],
)
def test_patch_rejects_invalid_input(api, change):
    client, holder = api
    holder["client"], table = _user_client([])
    res = client.patch(f"/api/deployments/{DEP_ID}/location", json={**BODY, **change})
    assert res.status_code == 422
    table.update.assert_not_called()


def test_patch_refuses_the_demo_user(api):
    from app.dependencies import get_current_user
    from app.main import app

    client, holder = api
    holder["client"], table = _user_client([])
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="demo", app_metadata={"is_demo": True})
    assert client.patch(f"/api/deployments/{DEP_ID}/location", json=BODY).status_code == 403
    table.update.assert_not_called()
