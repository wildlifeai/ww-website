# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""A project's GBIF dataset (#373): the key from a link, the GBIF calls (mocked), their cache,
GBIF not answering, and the settings route that saves the key."""

from datetime import date
from types import SimpleNamespace

import httpx
import pytest

from app.domain import gbf_report
from app.domain.gbif_dataset import GbifNotFound, dataset_summary, parse_dataset_key, save_dataset_key_as_user, with_gbif_figures
from app.services import gbif
from tests.fake_postgrest import FakeDB

KEY = "423e89b5-3c05-4ebe-8671-ddade9409b11"
P1 = "11111111-1111-1111-1111-111111111111"
DATASET = {"key": KEY, "title": "Ridge camera traps", "doi": "10.15468/abc123", "pubDate": "2026-03-19T00:00:00.000+00:00"}


@pytest.fixture
def gbif_api(monkeypatch):
    """GBIF behind a mock transport: answers from ``api.routes``, records every call."""
    api = SimpleNamespace(calls=[], status=200, down=False)

    def handler(req: httpx.Request):
        api.calls.append(req)
        if api.down:
            raise httpx.ConnectTimeout("timed out", request=req)
        if api.status != 200:
            return httpx.Response(api.status, text="Entity not found")
        if req.url.path == f"/v1/dataset/{KEY}":
            return httpx.Response(200, json=DATASET)
        if req.url.path == "/v1/occurrence/search":
            return httpx.Response(200, json={"offset": 0, "limit": 0, "count": 1234, "results": []})
        return httpx.Response(404, text="Entity not found")

    monkeypatch.setattr(gbif, "transport", httpx.MockTransport(handler))
    return api


# ── The key ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        KEY,
        f"  {KEY.upper()} ",
        f"https://www.gbif.org/dataset/{KEY}",
        f"https://www.gbif.org/dataset/{KEY}/metrics",
        f"gbif.org/dataset/{KEY}",
        f"https://api.gbif.org/v1/dataset/{KEY}",
    ],
)
def test_the_key_comes_from_a_key_or_a_gbif_link(text):
    assert parse_dataset_key(text) == KEY


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not a key",
        "10.15468/abc123",
        f"https://example.org/dataset/{KEY}",
        "https://www.gbif.org/dataset/abc",
        f"https://www.gbif.org/species/{KEY}",
    ],
)
def test_anything_else_has_no_key(text):
    assert parse_dataset_key(text) is None


# ── The GBIF calls ──────────────────────────────────────────────────


async def test_the_summary_reads_the_dataset_and_its_occurrence_count(gbif_api):
    assert await dataset_summary(KEY) == {
        "dataset_key": KEY,
        "title": "Ridge camera traps",
        "doi": "10.15468/abc123",
        "published_records": 1234,
        "last_published": "2026-03-19",
    }
    search = next(c for c in gbif_api.calls if c.url.path == "/v1/occurrence/search")
    assert search.url.params["datasetKey"] == KEY and search.url.params["limit"] == "0"
    assert all(str(c.url).startswith("https://api.gbif.org/v1/") for c in gbif_api.calls)


async def test_gbif_is_asked_once_a_day(gbif_api):
    await dataset_summary(KEY)
    await dataset_summary(KEY)
    assert len(gbif_api.calls) == 2  # the dataset and the count, once


async def test_a_failure_is_not_cached(gbif_api):
    gbif_api.down = True
    [row] = await with_gbif_figures([{"project_id": P1, "dataset_key": KEY}])
    assert row["status"] == "unavailable" and row["published_records"] is None
    gbif_api.down = False
    [row] = await with_gbif_figures([{"project_id": P1, "dataset_key": KEY}])
    assert row["status"] == "published" and row["published_records"] == 1234


async def test_the_figure_says_what_gbif_said(gbif_api):
    rows = await with_gbif_figures([{"project_id": "a", "dataset_key": KEY}, {"project_id": "b", "dataset_key": None}])
    assert [r["status"] for r in rows] == ["published", "not_published"]
    assert rows[0]["title"] == "Ridge camera traps" and rows[0]["last_published"] == "2026-03-19"
    gbif_api.status = 500
    [down] = await with_gbif_figures([{"project_id": "c", "dataset_key": "00000000-0000-0000-0000-000000000000"}])
    assert down["status"] == "unavailable"
    gbif_api.status = 404
    [gone] = await with_gbif_figures([{"project_id": "d", "dataset_key": "00000000-0000-0000-0000-000000000001"}])
    assert gone["status"] == "not_found"
    with pytest.raises(GbifNotFound):
        await dataset_summary("00000000-0000-0000-0000-000000000002")


async def test_the_report_carries_the_dataset(gbif_api, monkeypatch):
    db = FakeDB(
        {
            "projects": [{"id": P1, "name": "Alpha", "organisation_id": "o", "gbif_dataset_key": KEY, "deleted_at": None}],
            "deployments": [],
            "media": [],
            "observations": [],
            "taxa": [],
        }
    )
    monkeypatch.setattr(gbf_report, "create_service_client", db.client)
    r = await gbf_report.gbf_report([P1], date(2026, 1, 1), date(2026, 1, 31))
    assert r is not None
    [row] = r["target_21"]["datasets"]
    assert row["status"] == "published" and row["published_records"] == 1234 and row["reviewed_detections"] == 0


# ── Saving the key as the caller ────────────────────────────────────


def _user_client(updated: list, visible: list):
    """A user client whose update returns ``updated`` rows and whose select returns ``visible``."""
    sent = []

    class Query:
        def __init__(self, data):
            self.data = data

        def update(self, payload):
            sent.append(payload)
            return Query(updated)

        def select(self, *_):
            return Query(visible)

        def eq(self, *_):
            return self

        def limit(self, *_):
            return self

        def execute(self):
            return SimpleNamespace(data=self.data)

    return SimpleNamespace(table=lambda name: Query([]), sent=sent)


def test_the_save_reports_what_rls_did():
    admin = _user_client([{"id": P1}], [{"id": P1}])
    assert save_dataset_key_as_user(admin, P1, KEY) == "saved"
    assert admin.sent == [{"gbif_dataset_key": KEY}]
    assert save_dataset_key_as_user(_user_client([], [{"id": P1}]), P1, None) == "refused"
    assert save_dataset_key_as_user(_user_client([], []), P1, KEY) == "not_found"


@pytest.fixture
def route(monkeypatch, gbif_api):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.dependencies import get_current_user, get_user_client
    from app.main import app

    state = SimpleNamespace(client=None, user_client=_user_client([{"id": P1}], [{"id": P1}]), settings=settings, gbif=gbif_api)
    monkeypatch.setattr(settings, "FF_GBF_REPORT_ENABLED", True)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u1", email_confirmed_at="2026-01-01", app_metadata={})
    app.dependency_overrides[get_user_client] = lambda: state.user_client
    state.client = TestClient(app)
    yield state
    app.dependency_overrides.clear()


def _put(route, dataset):
    return route.client.put(f"/api/projects/{P1}/gbif-dataset", json={"dataset": dataset}, headers={"Authorization": "Bearer x"})


def test_an_admin_saves_a_link_as_its_key(route):
    res = _put(route, f"https://www.gbif.org/dataset/{KEY}")
    assert res.status_code == 200
    assert res.json()["data"]["gbif_dataset_key"] == KEY
    assert res.json()["data"]["dataset"]["title"] == "Ridge camera traps"
    assert route.user_client.sent == [{"gbif_dataset_key": KEY}]


def test_clearing_needs_no_gbif(route):
    res = _put(route, None)
    assert res.status_code == 200 and res.json()["data"]["gbif_dataset_key"] is None
    assert route.user_client.sent == [{"gbif_dataset_key": None}]
    assert route.gbif.calls == []


def test_nothing_is_saved_that_gbif_does_not_know(route):
    assert _put(route, "not a key").status_code == 422
    route.gbif.status = 404
    assert _put(route, KEY).status_code == 422
    route.gbif.status, route.gbif.down = 200, True
    assert _put(route, KEY).status_code == 503
    assert route.user_client.sent == []


def test_only_a_project_admin_saves(route):
    route.user_client = _user_client([], [{"id": P1}])
    assert _put(route, KEY).status_code == 403
    route.user_client = _user_client([], [])
    assert _put(route, KEY).status_code == 404


def test_the_demo_cannot_save(route):
    from app.dependencies import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="demo", email_confirmed_at="2026-01-01", app_metadata={"is_demo": True})
    assert _put(route, KEY).status_code == 403


def test_the_route_is_off_with_the_report(route, monkeypatch):
    monkeypatch.setattr(route.settings, "FF_GBF_REPORT_ENABLED", False)
    assert _put(route, KEY).json()["error"]["code"] == "FEATURE_DISABLED"
