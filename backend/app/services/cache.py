# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Redis cache-aside pattern.

Provides a simple ``cached()`` helper for caching expensive lookups
(SSCMA catalog, manifest input hashes, etc.) with configurable TTL.
"""

import json
from typing import Any, Callable, Awaitable, Optional

import redis.asyncio as redis
import structlog

from app.config import settings

logger = structlog.get_logger()

_redis: Optional[redis.Redis] = None


async def get_redis() -> redis.Redis:
    """Get or create a shared Redis connection."""
    global _redis
    if not _redis:
        _redis = redis.from_url(settings.REDIS_URL)
    return _redis


async def close_redis() -> None:
    """Cleanly close the Redis connection."""
    global _redis
    if _redis:
        await _redis.close()
        _redis = None


async def cached(
    key: str, ttl: int, fetch_fn: Callable[[], Awaitable[Any]]
) -> Any:
    """Cache-aside: return cached value or call fetch_fn and cache the result.

    Args:
        key: Redis key.
        ttl: Time-to-live in seconds.
        fetch_fn: Async callable that produces the value on cache miss.

    Returns:
        The cached or freshly-fetched value.
    """
    r = await get_redis()

    cached_val = await r.get(key)
    if cached_val:
        logger.debug("cache_hit", key=key)
        return json.loads(cached_val)

    logger.debug("cache_miss", key=key)
    result = await fetch_fn()
    await r.set(key, json.dumps(result), ex=ttl)
    return result
