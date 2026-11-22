# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The GPU batch wait in the job API (#460).

A job queued for the REANNZ batch (``WORKER_BACKEND=lambda``) carries ``batch_wait`` on
``GET /api/jobs/{id}`` and in ``GET /api/jobs``, so the upload dock and the Clusters page can
say it waits for the next GPU run, and for how long it usually waits. A leased job, and any job
on Azure, carries none. ``gpu_batch`` stays true for a batch job's whole life, so the dock does
not call it stalled between the cluster's reports.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.dependencies import get_current_user
from app.jobs import dispatch, store
from app.routers import jobs
from app.schemas.job import JobStatus
from tests.fake_postgrest import FakeDB

DEP = "d0000000-0000-4000-8000-000000000001"
JOB = "a0000000-0000-4000-8000-000000000001"
QUEUED_AT = "2026-11-22T01:00:00+00:00"


@pytest.fixture(autouse=True)
def _clean_memory():
    store._memory_store.clear()
    store._memory_events.clear()
    yield
    store._memory_store.clear()
    store._memory_events.clear()


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB({"api_jobs": []})
    monkeypatch.setattr(store, "create_service_client", fake.client)
    monkeypatch.setattr(settings, "BATCH_MAX_WAIT_MINUTES", 60)
    monkeypatch.setattr(settings, "BATCH_CRON_INTERVAL_MINUTES", 30)
    return fake


async def _queue_embed(monkeypatch) -> str:
    """Start the Wildlife Brain on a deployment as the router does, under the Lambda backend."""
    monkeypatch.setattr(settings, "WORKER_BACKEND", "lambda")
    job_id = await store.create_job(user_id="u1", kind="ai_embed", label="Embed", deployment_ids=[DEP])
    assert await dispatch.enqueue_job("embed_deployment_job", job_id, DEP, None) == "batch"
    return job_id


async def _summary(job_id: str) -> dict:
    listed, _ = await store.list_jobs("u1")
    return next(j for j in listed if j["job_id"] == job_id)


@pytest.mark.parametrize(
    ("max_wait", "cron", "usual"),
    [(60, 30, 120), (120, 30, 180), (60, 0, 60), (50, 0, 60), (20, 10, 30), (10, 0, 15), (0, 0, 0)],
)
def test_the_stated_wait_adds_the_cron_interval_and_rounds_up(monkeypatch, max_wait, cron, usual):
    monkeypatch.setattr(settings, "BATCH_MAX_WAIT_MINUTES", max_wait)
    monkeypatch.setattr(settings, "BATCH_CRON_INTERVAL_MINUTES", cron)
    assert store.usual_batch_wait_minutes() == usual


async def test_a_job_queued_for_the_batch_says_how_long_it_usually_waits(db, monkeypatch):
    job_id = await _queue_embed(monkeypatch)
    queued_at = db.rows("api_jobs")[0]["job_data"]["dispatch"]["queued_at"]

    job = await store.get_job(job_id)
    assert job is not None and job.batch_wait is not None and job.gpu_batch
    assert job.batch_wait.usual_wait_minutes == 120
    assert job.batch_wait.queued_at is not None and job.batch_wait.queued_at.isoformat() == queued_at
    assert (await _summary(job_id))["batch_wait"] == {"queued_at": queued_at, "usual_wait_minutes": 120}


async def test_the_wait_survives_a_fresh_process(db, monkeypatch):
    """After a refresh the API may be a new Lambda container: the wait comes from api_jobs alone."""
    job_id = await _queue_embed(monkeypatch)
    store._memory_store.clear()
    store._memory_events.clear()

    job = await store.get_job(job_id)
    assert job is not None and job.batch_wait is not None and job.batch_wait.usual_wait_minutes == 120


async def test_a_leased_job_no_longer_waits_and_a_requeued_one_waits_again(db, monkeypatch):
    job_id = await _queue_embed(monkeypatch)
    await store.patch_job_data(job_id, {"lease": {"id": "lease-1"}, "message": "Running on the GPU cluster (3 photos)"}, status=JobStatus.PROCESSING)

    leased = await store.get_job(job_id)
    assert leased is not None and leased.batch_wait is None
    assert leased.gpu_batch, "a leased job still runs on the cluster, which reports every few minutes"
    assert (await _summary(job_id))["batch_wait"] is None

    # The reaper requeues in another process, at least a minute later (Windows' clock can
    # return one instant for two calls this close, so the test moves it on itself).
    row = db.rows("api_jobs")[0]
    requeued = store.requeued_job_data(row["job_data"], "Requeued: the GPU cluster job stopped reporting.")
    requeued["updated_at"] = (datetime.fromisoformat(requeued["updated_at"]) + timedelta(minutes=1)).isoformat()
    row.update(status=JobStatus.QUEUED.value, job_data=requeued)
    job = await store.get_job(job_id)
    assert job is not None and job.status == JobStatus.QUEUED and job.batch_wait is not None


async def test_a_job_that_runs_at_once_carries_no_wait(db):
    """Azure: the ARQ worker or this process runs the job, and no dispatch record is written."""
    job_id = await store.create_job(user_id="u1", kind="ai_embed", label="Embed", deployment_ids=[DEP])
    await store.flush_pending_syncs(job_id)

    job = await store.get_job(job_id)
    assert job is not None and job.batch_wait is None and not job.gpu_batch
    assert (await _summary(job_id))["batch_wait"] is None


def test_both_job_routes_return_the_wait(db):
    jd = {
        "job_id": JOB,
        "status": "queued",
        "progress": 0.0,
        "created_at": QUEUED_AT,
        "updated_at": QUEUED_AT,
        "user_id": "u1",
        "kind": "ai_embed",
        "deployment_ids": [DEP],
        "dispatch": {"backend": "batch", "name": "embed_deployment_job", "kind": "embed", "queued_at": QUEUED_AT},
    }
    db.rows("api_jobs").append({"id": JOB, "status": "queued", "created_at": QUEUED_AT, "updated_at": QUEUED_AT, "job_data": jd})
    app = FastAPI()
    app.include_router(jobs.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u1", email="u@ww.ai")
    api = TestClient(app)

    one = api.get(f"/api/jobs/{JOB}")
    assert one.status_code == 200, one.text
    assert one.json()["data"]["batch_wait"]["usual_wait_minutes"] == 120
    assert one.json()["data"]["gpu_batch"] is True
    listed = api.get("/api/jobs")
    assert listed.status_code == 200, listed.text
    assert listed.json()["data"]["jobs"][0]["batch_wait"] == {"queued_at": QUEUED_AT, "usual_wait_minutes": 120}
