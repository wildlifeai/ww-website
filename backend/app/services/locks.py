# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Named mutual exclusion across processes: through Redis, or a lease row in ``api_jobs``.

``exclusive(name)`` always takes an in-process ``asyncio.Lock`` and, on top of it, a
cross-process lock: a Redis lock when ``REDIS_URL`` is set, otherwise a lease row in
``api_jobs`` (#323). Either has a TTL, renewed every third of it while the holder runs, so
a crashed holder frees the name within ``ttl_seconds``.

The guarantee is only as wide as the lock that was taken:

- ``REDIS_URL`` set and Redis reachable: one holder per name across all processes.
- ``REDIS_URL`` empty and Supabase reachable: one holder per name across all processes,
  including Cloud Run job executions, which share nothing but the database.
- Redis or Supabase unreachable when the lock is taken: one holder per name in this
  process only (logged as ``redis_lock_unavailable`` or ``db_lock_unavailable``).
- A holder that cannot renew for longer than the TTL can lose its lock while still running
  (logged as ``redis_lock_renew_failed`` or ``db_lock_renew_failed``).

The lease is one ``api_jobs`` row per name, with ``status = 'lock'`` and an id derived
from the name, so the table's primary key admits one row per name. Every step is a single
SQL statement through PostgREST, atomic on its own:

- acquire: INSERT the row; on a key conflict, UPDATE it only where its ``expires_at`` has
  passed. Concurrent updates of one row queue on the row lock and the later one re-checks
  the condition against the new expiry, so one taker wins.
- renew: UPDATE ``expires_at`` only where ``holder`` is still this holder.
- release: DELETE only where ``holder`` is still this holder, so a holder whose lease
  expired never frees its successor's.

Expiry is compared on the clients' clocks, so it assumes they agree to well within the TTL.
The status keeps lease rows out of every job reader (the reaper, KEDA, the coalescing).
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Awaitable, Callable

import structlog

from app.config import settings

logger = structlog.get_logger()

_KEY_PREFIX = "ww:lock:"
DEFAULT_TTL_SECONDS = 600
# How often a waiter polls Redis or the database for a held lock.
_POLL_SECONDS = 2.0

_LEASE_TABLE = "api_jobs"
_LEASE_STATUS = "lock"
# A lease row's id is uuid5(this, name): the same in every process. Job ids are uuid4, so
# the two never collide.
_LEASE_NAMESPACE = uuid.UUID("9972db51-36c0-4b0c-86ca-b4a6699d72b7")
_UNIQUE_VIOLATION = "23505"

# name -> [lock, holders and waiters]. An entry lives only while someone holds or waits
# on it, so the dict stays small and a lock never outlives the event loop it was bound to.
_local: dict[str, list] = {}


class LockBusy(Exception):
    """The lock is held elsewhere and the caller asked not to wait."""


class LeaseLost(Exception):
    """The database lease expired and another holder took it."""


def _redis_client():
    """A Redis client for the lock, or None when no Redis is configured."""
    if not settings.REDIS_URL:
        return None
    import redis.asyncio as aioredis

    return aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=2)


def _lease_client():
    """The Supabase client for the database lease (replaced in tests)."""
    from app.services.supabase_client import create_service_client

    return create_service_client()


async def _close(client) -> None:
    with contextlib.suppress(Exception):
        close = getattr(client, "aclose", None) or client.close
        await close()


async def _renew(refresh: Callable[[], Awaitable[object]], ttl_seconds: float, name: str, event: str) -> None:
    """Call ``refresh`` every third of the TTL while the holder runs."""
    while True:
        await asyncio.sleep(ttl_seconds / 3)
        try:
            await refresh()
        except Exception as exc:  # noqa: BLE001 - renewal is best-effort, the holder keeps running
            logger.warning(event, lock=name, error=str(exc))


@contextlib.asynccontextmanager
async def _redis_lock(client, name: str, *, wait: bool, ttl_seconds: float) -> AsyncIterator[None]:
    lock = client.lock(_KEY_PREFIX + name, timeout=ttl_seconds, sleep=_POLL_SECONDS, blocking=wait, blocking_timeout=None)
    try:
        acquired = await lock.acquire()
    except Exception as exc:  # noqa: BLE001 - Redis down must not stop the work, the local lock still holds
        logger.warning("redis_lock_unavailable", lock=name, error=str(exc))
        acquired = None
    if acquired is None:
        await _close(client)
        yield
        return
    if not acquired:
        await _close(client)
        raise LockBusy(name)
    renewer = asyncio.create_task(_renew(lock.reacquire, ttl_seconds, name, "redis_lock_renew_failed"))
    try:
        yield
    finally:
        renewer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await renewer
        try:
            await lock.release()
        except Exception as exc:  # noqa: BLE001 - an expired lock is already free
            logger.warning("redis_lock_release_failed", lock=name, error=str(exc))
        await _close(client)


# ── The database lease (no Redis) ─────────────────────────────────────


def lease_id(name: str) -> str:
    """The ``api_jobs`` id of ``name``'s lease row."""
    return str(uuid.uuid5(_LEASE_NAMESPACE, name))


def _utc_text(seconds_from_now: float = 0.0) -> str:
    """UTC time as fixed-width ISO text, so expiries compare correctly as text in PostgREST."""
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds_from_now)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _lease_data(name: str, holder: str, ttl_seconds: float) -> dict:
    return {"kind": "lock", "lock": name, "holder": holder, "expires_at": _utc_text(ttl_seconds)}


def _try_lease(client, name: str, holder: str, ttl_seconds: float) -> bool:
    """One attempt at the lease: True when ``holder`` now holds it, False when another live holder does."""
    from postgrest.exceptions import APIError

    data = _lease_data(name, holder, ttl_seconds)
    try:
        client.table(_LEASE_TABLE).insert({"id": lease_id(name), "status": _LEASE_STATUS, "job_data": data}).execute()
        return True
    except APIError as exc:
        if exc.code != _UNIQUE_VIOLATION:
            raise
    # The row exists. Take it over only if its lease has run out.
    resp = client.table(_LEASE_TABLE).update({"job_data": data}).eq("id", lease_id(name)).lt("job_data->>expires_at", _utc_text()).execute()
    return bool(resp.data)


def _refresh_lease(client, name: str, holder: str, ttl_seconds: float) -> None:
    resp = (
        client.table(_LEASE_TABLE)
        .update({"job_data": _lease_data(name, holder, ttl_seconds)})
        .eq("id", lease_id(name))
        .eq("job_data->>holder", holder)
        .execute()
    )
    if not resp.data:
        raise LeaseLost(name)


def _release_lease(client, name: str, holder: str) -> None:
    client.table(_LEASE_TABLE).delete().eq("id", lease_id(name)).eq("job_data->>holder", holder).execute()


@contextlib.asynccontextmanager
async def _db_lock(name: str, *, wait: bool, ttl_seconds: float) -> AsyncIterator[None]:
    client = _lease_client()
    if client is None:
        yield
        return
    holder = uuid.uuid4().hex
    while True:
        try:
            acquired = await asyncio.to_thread(_try_lease, client, name, holder, ttl_seconds)
        except Exception as exc:  # noqa: BLE001 - the database down must not stop the work, the local lock still holds
            logger.warning("db_lock_unavailable", lock=name, error=str(exc))
            acquired = None
        if acquired is not False or not wait:
            break
        await asyncio.sleep(_POLL_SECONDS)
    if acquired is None:
        yield
        return
    if not acquired:
        raise LockBusy(name)

    async def refresh() -> None:
        await asyncio.to_thread(_refresh_lease, client, name, holder, ttl_seconds)

    renewer = asyncio.create_task(_renew(refresh, ttl_seconds, name, "db_lock_renew_failed"))
    try:
        yield
    finally:
        renewer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await renewer
        try:
            await asyncio.to_thread(_release_lease, client, name, holder)
        except Exception as exc:  # noqa: BLE001 - the lease expires on its own
            logger.warning("db_lock_release_failed", lock=name, error=str(exc))


@contextlib.asynccontextmanager
async def _shared_lock(name: str, *, wait: bool, ttl_seconds: float) -> AsyncIterator[None]:
    """The cross-process lock: Redis when configured, the database lease otherwise."""
    client = _redis_client()
    lock = _redis_lock(client, name, wait=wait, ttl_seconds=ttl_seconds) if client is not None else _db_lock(name, wait=wait, ttl_seconds=ttl_seconds)
    async with lock:
        yield


@contextlib.asynccontextmanager
async def exclusive(name: str, *, wait: bool = True, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> AsyncIterator[None]:
    """Hold ``name`` for the body. Waits for the current holder, or raises ``LockBusy`` when ``wait`` is False."""
    entry = _local.setdefault(name, [asyncio.Lock(), 0])
    entry[1] += 1
    try:
        local: asyncio.Lock = entry[0]
        if not wait and local.locked():
            raise LockBusy(name)
        await local.acquire()
        try:
            async with _shared_lock(name, wait=wait, ttl_seconds=ttl_seconds):
                yield
        finally:
            local.release()
    finally:
        entry[1] -= 1
        if entry[1] == 0 and _local.get(name) is entry:
            del _local[name]
