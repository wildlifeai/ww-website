# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""A job's ``api_jobs`` row survives a dropped Supabase connection and the Lambda handover (#459).

On the AWS dev stack (2026-11-20) the first request after a Lambda environment sat frozen failed
with ``Server disconnected``, 17 times in one upload test, and the job store skipped it with a
debug line. A lost final write leaves a finished job looking unfinished until the reaper fails
it; a lost read makes a job look missing. And under ``WORKER_BACKEND=lambda`` the cpu function
could start before the background write of the job's row had landed.
"""

import json
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from structlog.testing import capture_logs

from app.config import settings
from app.jobs import dispatch, store
from app.schemas.job import JobStatus
from app.services import lambda_invoke
from tests.fake_postgrest import FakeDB

JOB = "job-1"
NEWER = "2026-11-21T00:00:00+00:00"


class _DroppedConnection:
    """The shared client once the server has closed its connection: every request fails."""

    def __init__(self, error: Exception | None = None):
        self.error = error or httpx.RemoteProtocolError("Server disconnected")
        self.requests = 0

    def table(self, name):
        return self

    def upsert(self, payload):
        return self

    def select(self, columns):
        return self

    def eq(self, column, value):
        return self

    def execute(self):
        self.requests += 1
        raise self.error


@pytest.fixture(autouse=True)
def _job_in_memory():
    """A job this process is running, half done."""
    job = {"job_id": JOB, "status": "processing", "progress": 0.5, "updated_at": "2026-11-20T00:00:00+00:00", "_next_seq": 0}
    store._memory_store.clear()
    store._memory_events.clear()
    store._memory_store[f"job:{JOB}"] = json.dumps(job)
    yield
    store._memory_store.clear()
    store._memory_events.clear()


def _clients(monkeypatch, first, after_reset) -> list[int]:
    """Serve ``first`` until the store resets the client, then ``after_reset``; returns the resets."""
    current = {"client": first}
    resets: list[int] = []

    def reset():
        resets.append(1)
        current["client"] = after_reset

    monkeypatch.setattr(store, "create_service_client", lambda: current["client"])
    monkeypatch.setattr(store, "reset_service_client", reset)
    return resets


def _db(status: str = "processing", updated_at: str = NEWER, **job_data) -> FakeDB:
    row = {"job_id": JOB, "status": status, "progress": 0.5, "updated_at": updated_at, "_next_seq": 0, **job_data}
    return FakeDB({"api_jobs": [{"id": JOB, "status": status, "job_data": row}]})


def _row(db: FakeDB) -> dict:
    return next(r for r in db.rows("api_jobs") if r["id"] == JOB)


def _skipped(logs: list[dict], event: str) -> list[str]:
    return [e["log_level"] for e in logs if e["event"] == event]


# ── Writes ───────────────────────────────────────────────────────────


async def test_final_write_after_a_dropped_connection_completes_the_job(monkeypatch):
    db = _db()
    dropped = _DroppedConnection()
    resets = _clients(monkeypatch, dropped, db.client())

    await store.update_job(JOB, status=JobStatus.COMPLETED, progress=1.0)

    assert dropped.requests == 1 and resets == [1]
    assert _row(db)["status"] == "completed"
    assert _row(db)["job_data"]["status"] == "completed"
    assert _row(db)["job_data"]["progress"] == 1.0


async def test_an_in_progress_write_is_retried_too(monkeypatch):
    db = FakeDB({"api_jobs": []})
    resets = _clients(monkeypatch, _DroppedConnection(), db.client())

    await store.update_job(JOB, progress=0.75)
    await store.flush_pending_syncs(JOB)

    assert resets == [1]
    assert _row(db)["job_data"]["progress"] == 0.75


async def test_a_final_write_lost_twice_is_a_warning(monkeypatch):
    second = _DroppedConnection()
    resets = _clients(monkeypatch, _DroppedConnection(), second)

    with capture_logs() as logs:
        await store.update_job(JOB, status=JobStatus.COMPLETED)

    assert resets == [1] and second.requests == 1  # one retry, not a loop
    assert [(e["log_level"], e["job_id"]) for e in logs if e["event"] == "supabase_sync_skipped"] == [("warning", JOB)]


async def test_an_in_progress_write_lost_twice_stays_quiet(monkeypatch):
    _clients(monkeypatch, _DroppedConnection(), _DroppedConnection())

    with capture_logs() as logs:
        await store.update_job(JOB, progress=0.75)
        await store.flush_pending_syncs(JOB)

    assert _skipped(logs, "supabase_sync_skipped") == ["debug"]


async def test_other_errors_are_not_retried(monkeypatch):
    refused = _DroppedConnection(ValueError("permission denied for table api_jobs"))
    resets = _clients(monkeypatch, refused, _DroppedConnection())

    with capture_logs() as logs:
        await store.update_job(JOB, status=JobStatus.FAILED, error="boom")

    assert refused.requests == 1 and resets == []
    assert _skipped(logs, "supabase_sync_skipped") == ["warning"]


# ── Reads ────────────────────────────────────────────────────────────


async def test_a_job_another_process_created_is_found_after_a_dropped_connection(monkeypatch):
    """The cpu function's first look at a job it was handed, after the environment sat frozen."""
    store._memory_store.clear()
    db = _db()
    resets = _clients(monkeypatch, _DroppedConnection(), db.client())

    await store.update_job(JOB, progress=0.75)
    await store.flush_pending_syncs(JOB)

    assert resets == [1]
    assert json.loads(store._memory_store[f"job:{JOB}"])["progress"] == 0.75
    assert _row(db)["job_data"]["progress"] == 0.75


async def test_a_job_read_lost_twice_is_a_warning(monkeypatch):
    store._memory_store.clear()
    resets = _clients(monkeypatch, _DroppedConnection(), _DroppedConnection())

    with capture_logs() as logs:
        assert await store.get_job(JOB) is None

    assert resets == [1]
    assert _skipped(logs, "job_hydrate_failed") == ["warning"]


async def test_a_poll_sees_the_other_process_finish_after_a_dropped_connection(monkeypatch):
    """The API's poll of a job the cpu function runs: its freshness read is retried too."""
    resets = _clients(monkeypatch, _DroppedConnection(), _db(status="completed", progress=1.0).client())

    job = await store.get_job(JOB)

    assert resets == [1]
    assert job is not None and job.status == JobStatus.COMPLETED and job.progress == 1.0


async def test_a_freshness_read_lost_twice_keeps_the_memory_copy_quietly(monkeypatch):
    _clients(monkeypatch, _DroppedConnection(), _DroppedConnection())

    with capture_logs() as logs:
        job = await store.get_job(JOB)

    assert job is not None and job.status == JobStatus.PROCESSING and job.progress == 0.5
    assert _skipped(logs, "job_refresh_failed") == ["debug"]


# ── The Lambda handover ──────────────────────────────────────────────


class _SlowWrites:
    """A service client whose writes take a moment, as a real round trip does."""

    def __init__(self, db: FakeDB):
        self.db = db

    def table(self, name):
        query = self.db.table(name)
        execute = query.execute

        def slow_execute():
            time.sleep(0.05)
            return execute()

        query.execute = slow_execute
        return query


async def test_the_job_row_is_written_before_the_cpu_function_is_invoked(monkeypatch):
    """create_job writes the row in the background; the cpu function must find it when it starts."""
    monkeypatch.setattr(settings, "WORKER_BACKEND", "lambda")
    monkeypatch.setattr(settings, "CPU_FUNCTION_NAME", "ww-dev-cpu")
    db = FakeDB({"api_jobs": []})
    monkeypatch.setattr(store, "create_service_client", lambda: _SlowWrites(db))
    rows_at_invoke: list[list[str]] = []

    async def invoke(name, args, kwargs):
        rows_at_invoke.append([r["id"] for r in db.rows("api_jobs")])

    monkeypatch.setattr(lambda_invoke, "invoke_job", AsyncMock(side_effect=invoke))

    job_id = await store.create_job(user_id="u1", kind="export", label="CamtrapDP export with photos")
    assert await dispatch.enqueue_job("export_camtrapdp_originals_job", job_id, {"p": 1}, {"c": 2}) == "lambda"

    assert rows_at_invoke == [[job_id]]
