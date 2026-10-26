# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""GET /api/jobs: the caller's jobs, filtered by status and kind, a page at a time (UI audit 01 E1).

"Load more" follows ``next_before``, a cursor on created time with ties broken on id, so a
page neither skips nor repeats a job that shares its created time with another.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_current_user
from app.jobs import store
from app.routers import jobs
from tests.fake_postgrest import FakeDB


def _job(job_id, created_at, status="completed", kind="upload", user_id="u1"):
    return {
        "id": job_id,
        "status": status,
        "created_at": created_at,
        "updated_at": created_at,
        "job_data": {"user_id": user_id, "kind": kind, "label": job_id, "status": status},
    }


def _uuid(n):
    return f"00000000-0000-4000-8000-{n:012d}"


# Newest first: 7, then 6 and 5 (one created time, so id decides), 4 and 3 (same), 2, 1.
U1 = [
    _job(_uuid(1), "2026-10-20T01:00:00+00:00", status="failed"),
    _job(_uuid(2), "2026-10-20T02:00:00.5+00:00", kind="ai_pipeline"),
    _job(_uuid(3), "2026-10-20T03:00:00.123456+00:00", status="processing", kind="ai_pipeline"),
    _job(_uuid(4), "2026-10-20T03:00:00.123456+00:00"),
    _job(_uuid(5), "2026-10-20T04:00:00+00:00", status="queued"),
    _job(_uuid(6), "2026-10-20T04:00:00+00:00", status="completed_with_errors"),
    _job(_uuid(7), "2026-10-20T05:00:00+00:00"),
]
NEWEST_FIRST = [_uuid(n) for n in (7, 6, 5, 4, 3, 2, 1)]


@pytest.fixture
def client(monkeypatch):
    other = _job(_uuid(8), "2026-10-20T06:00:00+00:00", user_id="u2")
    lock = {"id": _uuid(9), "status": "lock", "created_at": "2026-10-20T07:00:00+00:00", "job_data": {}}
    monkeypatch.setattr(store, "create_service_client", FakeDB({"api_jobs": [*U1, other, lock]}).client)
    app = FastAPI()
    app.include_router(jobs.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u1", email="u@ww.ai")
    return TestClient(app)


def _pages(client, query):
    """Every page of a read, following next_before; returns the job ids of each page."""
    pages, before = [], None
    while True:
        r = client.get("/api/jobs", params=query + ([("before", before)] if before else []))
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        pages.append([j["job_id"] for j in data["jobs"]])
        before = data["next_before"]
        if before is None:
            return pages
        assert len(pages) < 10, "the cursor does not advance"


def test_load_more_reads_every_job_once_newest_first(client):
    pages = _pages(client, [("limit", 2)])
    assert pages == [NEWEST_FIRST[0:2], NEWEST_FIRST[2:4], NEWEST_FIRST[4:6], NEWEST_FIRST[6:]]


def test_a_full_last_page_has_no_cursor(client):
    assert _pages(client, [("limit", 7)]) == [NEWEST_FIRST]


def test_status_filter_runs_in_the_read_and_pages(client):
    pages = _pages(client, [("limit", 1), ("status", "queued"), ("status", "processing")])
    assert pages == [[_uuid(5)], [_uuid(3)]]


def test_kind_filter(client):
    assert _pages(client, [("kind", "ai_pipeline")]) == [[_uuid(3), _uuid(2)]]


def test_bad_filters_and_cursors_are_refused(client):
    assert client.get("/api/jobs", params={"status": "lock"}).status_code == 422
    assert client.get("/api/jobs", params={"kind": "upload,status.eq.x"}).status_code == 422
    assert client.get("/api/jobs", params={"before": "2026-10-20T05:00:00Z,id.lt.x"}).status_code == 422


def test_cursor_is_utc_to_the_microsecond():
    assert store.job_cursor({"id": "j", "created_at": "2026-10-20T04:00:00+00:00"}) == "2026-10-20T04:00:00.000000Z|j"
    assert store.job_cursor({"id": "j", "created_at": "2026-10-20T17:00:00.5+13:00"}) == "2026-10-20T04:00:00.500000Z|j"
    assert store.job_cursor({"id": "j", "created_at": None}) is None
