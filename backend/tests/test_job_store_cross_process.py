# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Job store: cross-process hydration, freshness refresh, coalescing lookup, stale reaper.

These guard the fixes for the "stuck Processing banner": the worker process must be able to
update jobs created on the API process (hydration), the API must see the worker's writes
(refresh-if-newer), chunked uploads must coalesce onto queued AI jobs, and orphaned jobs
must eventually be failed by the reaper.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.jobs import store
from app.schemas.job import JobStatus


class _Result:
    def __init__(self, data):
        self.data = data


@pytest.fixture(autouse=True)
def _clean_memory():
    store._memory_store.clear()
    store._memory_events.clear()
    yield
    store._memory_store.clear()
    store._memory_events.clear()


def _db_client(job_rows_by_id: dict[str, dict], recorded: list | None = None):
    """Mock service client: select returns the job row; update/upsert are recorded."""
    client = MagicMock()

    def table(name):
        t = MagicMock()
        state = {"id": None}

        def _eq(col, val):
            if col == "id":
                state["id"] = val
            return t

        t.select.return_value = t
        t.eq.side_effect = _eq
        t.in_.return_value = t
        t.order.return_value = t
        t.limit.return_value = t

        def _execute():
            row = job_rows_by_id.get(state["id"])
            if state["id"] is None:  # unfiltered list query (reaper)
                return _Result([{"id": k, **v} for k, v in job_rows_by_id.items()])
            return _Result([row] if row else [])

        t.execute.side_effect = _execute

        def _write(payload):
            # Record the target id lazily: the code chains .update(payload).eq("id", x),
            # so the id is only known when .eq fires on the returned write object.
            entry = [name, state["id"], payload]
            if recorded is not None:
                recorded.append(entry)
            w = MagicMock()

            def _weq(col, val):
                if col == "id":
                    entry[1] = val
                return w

            w.eq.side_effect = _weq
            w.execute.return_value = _Result([])
            return w

        t.update.side_effect = _write
        t.upsert.side_effect = _write
        return t

    client.table.side_effect = table
    return client


# ── Cross-process hydration: worker updating an API-created job ──────────────


async def test_update_job_hydrates_from_supabase(monkeypatch):
    """update_job on a process that never created the job (the worker) must hydrate from
    Supabase and apply the update — previously it silently no-opped, leaving AI jobs
    'queued' forever."""
    job_id = "j-1"
    db = {job_id: {"job_data": {"job_id": job_id, "status": "queued", "progress": 0.0, "updated_at": "2026-01-01T00:00:00+00:00", "_next_seq": 0}}}
    monkeypatch.setattr(store, "create_service_client", lambda: _db_client(db))

    assert f"job:{job_id}" not in store._memory_store  # cross-process: nothing in memory
    await store.update_job(job_id, status=JobStatus.PROCESSING, progress=0.5)

    data = json.loads(store._memory_store[f"job:{job_id}"])
    assert data["status"] == "processing"
    assert data["progress"] == 0.5


async def test_get_job_picks_up_newer_supabase_copy(monkeypatch):
    """A non-terminal job in this process's memory must refresh from Supabase when the DB
    copy is newer (the worker completed it) — otherwise the dock polls 'queued' forever."""
    job_id = "j-2"
    stale = {"job_id": job_id, "status": "queued", "progress": 0.0, "updated_at": "2026-01-01T00:00:00+00:00"}
    store._memory_store[f"job:{job_id}"] = json.dumps(stale)
    fresh = {**stale, "status": "completed", "progress": 1.0, "updated_at": "2026-01-02T00:00:00+00:00", "events": []}
    monkeypatch.setattr(store, "create_service_client", lambda: _db_client({job_id: {"job_data": fresh}}))

    job = await store.get_job(job_id)
    assert job is not None
    assert job.status == JobStatus.COMPLETED
    assert job.progress == 1.0


async def test_get_job_does_not_clobber_newer_memory(monkeypatch):
    """When memory is newer than the DB (job running in THIS process), get_job must keep it."""
    job_id = "j-3"
    newer = {"job_id": job_id, "status": "processing", "progress": 0.7, "updated_at": "2026-01-03T00:00:00+00:00"}
    store._memory_store[f"job:{job_id}"] = json.dumps(newer)
    older = {**newer, "status": "queued", "progress": 0.0, "updated_at": "2026-01-01T00:00:00+00:00", "events": []}
    monkeypatch.setattr(store, "create_service_client", lambda: _db_client({job_id: {"job_data": older}}))

    job = await store.get_job(job_id)
    assert job.status == JobStatus.PROCESSING
    assert job.progress == 0.7


# ── Coalescing lookup ─────────────────────────────────────────────────────────


async def test_find_active_ai_jobs_returns_queued_and_processing_with_status(monkeypatch):
    rows = {
        "a": {"status": "queued", "job_data": {"kind": "ai_pipeline", "deployment_ids": ["d1", "d2"]}},
        "b": {"status": "processing", "job_data": {"kind": "ai_pipeline", "deployment_ids": ["d3"]}},
    }
    client = _db_client(rows)
    tables: list = []
    make_table = client.table.side_effect
    client.table.side_effect = lambda name: tables.append(make_table(name)) or tables[-1]
    monkeypatch.setattr(store, "create_service_client", lambda: client)

    jobs = await store.find_active_ai_jobs()
    assert jobs == [
        {"job_id": "a", "status": "queued", "deployment_ids": ["d1", "d2"]},
        {"job_id": "b", "status": "processing", "deployment_ids": ["d3"]},
    ]
    # Processing jobs are read too, so the upload can queue one follow-up behind them (#284).
    tables[0].in_.assert_called_once_with("status", ["queued", "processing"])


# ── Reaper ────────────────────────────────────────────────────────────────────


async def test_reap_stale_jobs(monkeypatch):
    """Stale queued/processing jobs are failed; recently-updated ones are left alone."""
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    recent = datetime.now(timezone.utc).isoformat()
    rows = {
        "stale": {"job_data": {"kind": "ai_pipeline", "status": "queued", "updated_at": old}},
        "fresh": {"job_data": {"kind": "upload", "status": "processing", "updated_at": recent}},
    }
    recorded: list = []
    monkeypatch.setattr(store, "create_service_client", lambda: _db_client(rows, recorded))

    reaped = await store.reap_stale_jobs(max_age_minutes=60)

    assert reaped == 1
    failed_ids = [rid for (_, rid, payload) in recorded if payload.get("status") == "failed"]
    assert failed_ids == ["stale"]
    # The reaped job's memory copy is consistent too.
    assert json.loads(store._memory_store["job:stale"])["status"] == "failed"
