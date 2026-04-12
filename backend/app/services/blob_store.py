# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Temporary blob storage in Redis for passing files between API and worker.

Used for model conversion: the API process stores the uploaded ZIP in Redis,
the worker retrieves it by job_id, processes it, then deletes it.

Blobs auto-expire after 1 hour as a safety net.
"""

import redis.asyncio as redis

from app.config import settings

import structlog

logger = structlog.get_logger()

BLOB_TTL = 3600  # 1 hour — safety net expiry
BLOB_PREFIX = "blob:"


async def _get_redis() -> redis.Redis:
    return redis.from_url(settings.REDIS_URL)


async def store_blob(key: str, data: bytes, metadata: dict | None = None) -> None:
    """Store a binary blob in Redis with auto-expiry.

    Args:
        key: Unique key (typically job_id).
        data: Raw bytes to store.
        metadata: Optional JSON-serializable metadata stored alongside.
    """
    r = await _get_redis()
    await r.set(f"{BLOB_PREFIX}{key}:data", data, ex=BLOB_TTL)

    if metadata:
        import json
        await r.set(f"{BLOB_PREFIX}{key}:meta", json.dumps(metadata), ex=BLOB_TTL)

    await r.close()
    logger.debug("blob_stored", key=key, size_bytes=len(data))


async def retrieve_blob(key: str) -> tuple[bytes | None, dict | None]:
    """Retrieve a blob and its metadata from Redis.

    Returns:
        Tuple of (data_bytes, metadata_dict). Either may be None.
    """
    r = await _get_redis()
    data = await r.get(f"{BLOB_PREFIX}{key}:data")
    meta_raw = await r.get(f"{BLOB_PREFIX}{key}:meta")
    await r.close()

    metadata = None
    if meta_raw:
        import json
        metadata = json.loads(meta_raw)

    return data, metadata


async def delete_blob(key: str) -> None:
    """Delete a blob and its metadata from Redis."""
    r = await _get_redis()
    await r.delete(f"{BLOB_PREFIX}{key}:data", f"{BLOB_PREFIX}{key}:meta")
    await r.close()
    logger.debug("blob_deleted", key=key)
