# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Named mutual exclusion: across every process that shares Redis, in-process otherwise.

``exclusive(name)`` always takes an in-process ``asyncio.Lock`` and, when ``REDIS_URL``
is set, a Redis lock on top, so the two uvicorn workers and every ARQ worker replica
exclude each other. The Redis lock has a TTL (renewed while the holder runs) so a
crashed holder frees the name within ``ttl_seconds``.

The guarantee is only as wide as the lock that was taken:

- ``REDIS_URL`` set and Redis reachable: one holder per name across all processes.
- ``REDIS_URL`` empty, or Redis unreachable when the lock is taken: one holder per name
  in this process only (logged as ``redis_lock_unavailable``). That covers the dev image,
  where the API is one process and runs every job in-process.
- A holder that loses Redis for longer than the TTL can lose its lock while still running
  (logged as ``redis_lock_renew_failed``).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import AsyncIterator

import structlog

from app.config import settings

logger = structlog.get_logger()

_KEY_PREFIX = "ww:lock:"
DEFAULT_TTL_SECONDS = 600
# How often a waiter polls Redis for a held lock.
_POLL_SECONDS = 2.0

# name -> [lock, holders and waiters]. An entry lives only while someone holds or waits
# on it, so the dict stays small and a lock never outlives the event loop it was bound to.
_local: dict[str, list] = {}


class LockBusy(Exception):
    """The lock is held elsewhere and the caller asked not to wait."""


def _redis_client():
    """A Redis client for the lock, or None when no Redis is configured."""
    if not settings.REDIS_URL:
        return None
    import redis.asyncio as aioredis

    return aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=2)


async def _close(client) -> None:
    with contextlib.suppress(Exception):
        close = getattr(client, "aclose", None) or client.close
        await close()


async def _renew(lock, ttl_seconds: int, name: str) -> None:
    """Reset the Redis lock's TTL every third of it while the holder runs."""
    while True:
        await asyncio.sleep(ttl_seconds / 3)
        try:
            await lock.reacquire()
        except Exception as exc:  # noqa: BLE001 - renewal is best-effort, the holder keeps running
            logger.warning("redis_lock_renew_failed", lock=name, error=str(exc))


@contextlib.asynccontextmanager
async def _redis_lock(name: str, *, wait: bool, ttl_seconds: int) -> AsyncIterator[None]:
    client = _redis_client()
    if client is None:
        yield
        return
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
    renewer = asyncio.create_task(_renew(lock, ttl_seconds, name))
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


@contextlib.asynccontextmanager
async def exclusive(name: str, *, wait: bool = True, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> AsyncIterator[None]:
    """Hold ``name`` for the body. Waits for the current holder, or raises ``LockBusy`` when ``wait`` is False."""
    entry = _local.setdefault(name, [asyncio.Lock(), 0])
    entry[1] += 1
    try:
        local: asyncio.Lock = entry[0]
        if not wait and local.locked():
            raise LockBusy(name)
        await local.acquire()
        try:
            async with _redis_lock(name, wait=wait, ttl_seconds=ttl_seconds):
                yield
        finally:
            local.release()
    finally:
        entry[1] -= 1
        if entry[1] == 0 and _local.get(name) is entry:
            del _local[name]
