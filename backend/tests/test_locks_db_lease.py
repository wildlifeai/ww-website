# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The database lease behind ``services.locks`` when no Redis is configured (#323).

The fake below models what the lease relies on in Postgres: the ``api_jobs`` primary key
admits one row per id, and each PostgREST request is one statement that checks its filters
and writes atomically.
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock

import pytest
from postgrest.exceptions import APIError
from structlog.testing import capture_logs

from app.domain.pipeline import PipelineBusyError, deployment_lock_name, run_pipeline
from app.schemas.pipeline import PipelineStepType
from app.services import locks

DEP = "d0000000-0000-4000-8000-000000000323"


class _R:
    def __init__(self, data):
        self.data = data


def _field(row: dict, column: str):
    if "->>" in column:
        col, key = column.split("->>")
        value = (row.get(col) or {}).get(key)
        return None if value is None else str(value)
    return row.get(column)


class _Statement:
    def __init__(self, db: "_LeaseDb"):
        self.db, self.op, self.payload, self.preds = db, None, None, []

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def update(self, values):
        self.op, self.payload = "update", values
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, column, value):
        self.preds.append(lambda r: _field(r, column) == value)
        return self

    def lt(self, column, value):
        self.preds.append(lambda r: (v := _field(r, column)) is not None and v < value)
        return self

    def execute(self):
        with self.db.mutex:  # one statement at a time, as the row lock serialises them
            self.db.statements.append(self.op)
            if self.db.fail:
                raise ConnectionError("supabase down")
            rows = self.db.rows
            if self.op == "insert":
                if self.payload["id"] in rows:
                    raise APIError({"code": "23505", "message": "duplicate key value violates unique constraint"})
                rows[self.payload["id"]] = dict(self.payload)
                return _R([dict(self.payload)])
            hit = [r for r in rows.values() if all(p(r) for p in self.preds)]
            for r in hit:
                if self.op == "update":
                    r.update(self.payload)
                else:
                    del rows[r["id"]]
            return _R([dict(r) for r in hit])


class _LeaseDb:
    """``api_jobs`` as the lease uses it: insert, conditional update, conditional delete."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.mutex = threading.Lock()
        self.statements: list[str] = []
        self.fail = False

    def table(self, name):
        assert name == "api_jobs"
        return _Statement(self)

    def lease(self, name: str) -> dict | None:
        return self.rows.get(locks.lease_id(name))

    def hold(self, name: str, holder: str, expires_in: float) -> None:
        """A lease row written by another process."""
        self.rows[locks.lease_id(name)] = {
            "id": locks.lease_id(name),
            "status": "lock",
            "job_data": {"kind": "lock", "lock": name, "holder": holder, "expires_at": locks._utc_text(expires_in)},
        }


@pytest.fixture
def db(monkeypatch):
    fake = _LeaseDb()
    monkeypatch.setattr(locks, "_lease_client", lambda: fake)
    monkeypatch.setattr(locks, "_POLL_SECONDS", 0.01)
    return fake


async def test_the_lease_is_a_lock_row_while_held_and_gone_after(db):
    async with locks.exclusive("x"):
        row = db.lease("x")
        assert row["status"] == "lock"
        assert row["job_data"]["kind"] == "lock" and row["job_data"]["lock"] == "x"
        assert row["job_data"]["expires_at"] > locks._utc_text(590)
    assert db.rows == {}


async def test_a_live_lease_from_another_process_refuses_a_caller_that_will_not_wait(db):
    db.hold(deployment_lock_name(DEP), "another execution", expires_in=600)

    # This process holds nothing, so only the database can say no.
    with pytest.raises(PipelineBusyError):
        await run_pipeline(DEP, [PipelineStepType.MEDIA_PREP], wait=False)
    assert db.lease(deployment_lock_name(DEP))["job_data"]["holder"] == "another execution"


async def test_a_waiter_runs_once_the_other_process_releases(db):
    db.hold("x", "another execution", expires_in=600)
    entered = asyncio.Event()

    async def wait_then_hold():
        async with locks.exclusive("x"):
            entered.set()

    waiting = asyncio.create_task(wait_then_hold())
    await asyncio.sleep(0.05)
    assert not entered.is_set()
    db.rows.clear()  # the other execution released
    await asyncio.wait_for(waiting, 1)
    assert entered.is_set() and db.rows == {}


async def test_an_expired_lease_is_taken_over(db):
    db.hold("x", "a crashed execution", expires_in=-1)

    async with locks.exclusive("x", wait=False):
        holder = db.lease("x")["job_data"]["holder"]
        assert holder != "a crashed execution"
    assert db.rows == {}


async def test_the_lease_is_released_when_the_body_fails(db):
    with pytest.raises(RuntimeError, match="step failed"):
        async with locks.exclusive("x"):
            raise RuntimeError("step failed")
    assert db.rows == {}
    async with locks.exclusive("x", wait=False):  # free again at once
        pass


async def test_the_lease_is_renewed_while_held(db):
    async with locks.exclusive("x", ttl_seconds=0.06):
        first = db.lease("x")["job_data"]["expires_at"]
        await asyncio.sleep(0.1)
        assert db.lease("x")["job_data"]["expires_at"] > first
    assert db.statements.count("update") >= 2 and db.rows == {}


async def test_a_holder_that_lost_its_lease_leaves_the_new_holder_alone(db):
    with capture_logs() as logs:
        async with locks.exclusive("x", ttl_seconds=0.06):
            db.lease("x")["job_data"]["holder"] = "the next execution"  # expired and taken over
            await asyncio.sleep(0.05)

    assert db.lease("x")["job_data"]["holder"] == "the next execution"
    assert any(e["event"] == "db_lock_renew_failed" for e in logs)


def _race(db: _LeaseDb, n: int) -> list[bool]:
    """``n`` processes try the lease at the same moment."""
    barrier = threading.Barrier(n)
    results: list[bool] = [False] * n

    def attempt(i):
        barrier.wait()
        results[i] = locks._try_lease(db, "x", f"holder {i}", 600)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


@pytest.mark.parametrize("existing", [None, "expired"])
def test_concurrent_acquirers_get_one_winner(db, existing):
    for _ in range(20):
        db.rows.clear()
        if existing:
            db.hold("x", "a crashed execution", expires_in=-1)
        results = _race(db, 8)
        assert results.count(True) == 1
        assert db.lease("x")["job_data"]["holder"] == f"holder {results.index(True)}"


async def test_two_processes_never_hold_the_lease_together(db):
    """Two executions on one deployment, each with its own in-process lock (so only the lease is shared)."""
    inside: list[str] = []
    overlaps: list[bool] = []

    async def execution(tag):
        async with locks._db_lock("x", wait=True, ttl_seconds=600):
            overlaps.append(bool(inside))
            inside.append(tag)
            await asyncio.sleep(0.03)
            inside.remove(tag)

    await asyncio.gather(execution("a"), execution("b"), execution("c"))
    assert overlaps == [False, False, False] and db.rows == {}


async def test_the_database_down_falls_back_to_the_in_process_lock(db):
    db.fail = True
    ran = False
    with capture_logs() as logs:
        async with locks.exclusive("x"):
            ran = True
    assert ran
    assert any(e["event"] == "db_lock_unavailable" for e in logs)


async def test_redis_stays_first_when_configured(db, monkeypatch):
    client = MagicMock()
    lock = MagicMock()
    client.lock.return_value = lock

    async def acquire():
        return True

    async def done(*_a):
        return None

    lock.acquire, lock.release, lock.reacquire, client.aclose = acquire, done, done, done
    monkeypatch.setattr(locks, "_redis_client", lambda: client)

    async with locks.exclusive("x"):
        pass
    assert client.lock.called and db.statements == []


def test_lease_ids_are_stable_and_never_a_job_id():
    assert locks.lease_id("x") == locks.lease_id("x") != locks.lease_id("y")
    assert locks.lease_id(deployment_lock_name(DEP))[14] == "5"  # uuid5; job ids are uuid4
