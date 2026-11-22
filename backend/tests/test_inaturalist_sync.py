# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The iNaturalist sync as a job (#455): the route answers with a job id, and the job runs on the
cpu function on AWS and in the API process on Azure, carrying the user's id and no token."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.domain import inaturalist_sync
from app.domain.inaturalist import INatDomainError
from app.jobs import dispatch, inat_sync, store
from app.schemas.job import JobStatus
from app.services import lambda_invoke

JOB = "a0000000-0000-4000-8000-000000000455"


@pytest.fixture
def api(monkeypatch):
    from app.dependencies import get_current_user
    from app.main import app

    monkeypatch.setattr(settings, "FF_INAT_ENABLED", True)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u1")
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_sync_route_answers_with_a_job_and_does_no_sync_itself(api, monkeypatch):
    create_job = AsyncMock(return_value=JOB)
    enqueue_job = AsyncMock(return_value="local")
    monkeypatch.setattr("app.routers.inaturalist.create_job", create_job)
    monkeypatch.setattr("app.routers.inaturalist.enqueue_job", enqueue_job)
    monkeypatch.setattr(inaturalist_sync, "sync_inat_identifications", AsyncMock(side_effect=AssertionError("ran in the request")))

    resp = api.post("/api/inat/sync")

    assert resp.status_code == 200
    assert resp.json()["data"] == {"job_id": JOB, "status": "queued"}
    assert create_job.await_args.kwargs == {"user_id": "u1", "kind": "inat_sync", "label": "iNaturalist sync"}
    # Left out of Processing history and the other job lists; Settings follows it by id.
    assert "inat_sync" in store.UNLISTED_KINDS
    enqueue_job.assert_awaited_once_with("sync_inat_job", JOB, "u1")


def test_sync_route_is_off_with_the_flag(api, monkeypatch):
    monkeypatch.setattr(settings, "FF_INAT_ENABLED", False)
    assert api.post("/api/inat/sync").status_code == 404


async def test_on_lambda_the_sync_job_goes_to_the_cpu_function_with_no_token(monkeypatch):
    """A task left in the api function stops when it answers, so the job is invoked instead."""
    monkeypatch.setattr(settings, "WORKER_BACKEND", "lambda")
    monkeypatch.setattr(settings, "CPU_FUNCTION_NAME", "ww-dev-cpu")
    invoke = AsyncMock()
    monkeypatch.setattr(lambda_invoke, "invoke_job", invoke)
    local = []
    monkeypatch.setattr("app.jobs.runner.enqueue_local_job", lambda coro: local.append(coro.close()))

    assert await dispatch.enqueue_job("sync_inat_job", JOB, "u1") == "lambda"

    invoke.assert_awaited_once_with("sync_inat_job", (JOB, "u1"), {})
    assert local == []


async def test_on_azure_the_sync_job_stays_in_the_api_process_even_with_redis(monkeypatch):
    """Not the GPU worker: a sync should not cold-start a T4."""
    monkeypatch.setattr(settings, "WORKER_BACKEND", "local")
    monkeypatch.setattr(settings, "REDIS_URL", "redis://localhost:6379")
    ran = []
    monkeypatch.setattr("app.jobs.runner.enqueue_local_job", lambda coro: ran.append(coro.close()))
    assert await dispatch.enqueue_job("sync_inat_job", JOB, "u1") == "local"
    assert len(ran) == 1


def test_the_lambda_event_handler_accepts_the_sync_job():
    from app.jobs import definitions

    assert definitions.sync_inat_job in definitions.JOBS


def _updates(monkeypatch) -> list[dict]:
    writes: list[dict] = []

    async def update_job(job_id, **fields):
        writes.append({"job_id": job_id, **fields})

    monkeypatch.setattr(inat_sync, "update_job", update_job)
    return writes


async def test_sync_job_reports_progress_and_ends_with_the_counts(monkeypatch):
    writes = _updates(monkeypatch)
    counts = {"checked": 3, "updated": 2, "research": 1, "disagreement": 0, "observations_written": 0}

    async def sync(user_id, progress):
        assert user_id == "u1"
        await progress(0, 3)
        return counts

    monkeypatch.setattr(inaturalist_sync, "sync_inat_identifications", sync)
    await inat_sync.sync_inat_job(JOB, "u1")

    assert writes[0]["status"] == JobStatus.PROCESSING
    assert writes[1]["message"] == "Checked 0 of 3 observations…"
    assert writes[-1]["status"] == JobStatus.COMPLETED
    assert writes[-1]["message"] == "Synced 2 of 3: 1 research grade, 0 disagreement, 0 community IDs written."


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (INatDomainError("Failed to batch fetch observations"), "Failed to batch fetch observations"),
        (RuntimeError("Server disconnected"), "The sync failed. Try again later."),
    ],
)
async def test_a_failed_sync_fails_the_job(monkeypatch, error, expected):
    writes = _updates(monkeypatch)
    monkeypatch.setattr(inaturalist_sync, "sync_inat_identifications", AsyncMock(side_effect=error))
    await inat_sync.sync_inat_job(JOB, "u1")
    assert writes[-1]["status"] == JobStatus.FAILED
    assert writes[-1]["error"] == expected


class _Rows:
    """Just enough of a supabase query for sync_inat_identifications' read and updates."""

    def __init__(self, rows: list[dict], updates: list[dict]):
        self.rows, self.updates, self.patch = rows, updates, None

    @property
    def not_(self):
        return self

    def select(self, *_args):
        return self

    def is_(self, *_args):
        return self

    def in_(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def limit(self, *_args):
        return self

    def update(self, patch):
        self.patch = patch
        return self

    def execute(self):
        if self.patch is not None:
            self.updates.append(self.patch)
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=self.rows)


async def test_sync_reports_each_observation_before_it(monkeypatch):
    rows = [{"id": f"r{i}", "deployment_id": "d1", "user_id": "u1", "inat_observation_id": i, "species_guess": None} for i in (1, 2)]
    updates: list[dict] = []
    monkeypatch.setattr(inaturalist_sync, "create_service_client", lambda: SimpleNamespace(table=lambda _name: _Rows(rows, updates)))

    async def poll(ids):
        return [{"id": i, "quality_grade": "needs_id", "community_taxon": None} for i in ids]

    monkeypatch.setattr(inaturalist_sync, "batch_poll_observations", poll)
    seen: list[tuple[int, int]] = []

    async def progress(done, total):
        seen.append((done, total))

    result = await inaturalist_sync.sync_inat_identifications(user_id="u1", progress=progress)

    assert seen == [(0, 2), (1, 2)]
    assert (result["checked"], result["updated"]) == (2, 2)
    assert [u["sync_status"] for u in updates] == ["needs_id", "needs_id"]
