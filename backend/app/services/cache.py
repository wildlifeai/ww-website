# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Redis cache-aside pattern.

Provides a simple ``cached()`` helper for caching expensive lookups
(SSCMA catalog, manifest input hashes, etc.) with configurable TTL.
"""

import time
from typing import Any, Awaitable, Callable, Dict, Tuple

import structlog

logger = structlog.get_logger()

# In-memory dictionary for TTL cache: { key: (expiry_timestamp, value) }
_memory_cache: Dict[str, Tuple[float, Any]] = {}


async def cached(key: str, ttl: int, fetch_fn: Callable[[], Awaitable[Any]]) -> Any:
    """Cache-aside: return cached value or call fetch_fn and cache the result.

    Args:
        key: Cache key.
        ttl: Time-to-live in seconds.
        fetch_fn: Async callable that produces the value on cache miss.

    Returns:
        The cached or freshly-fetched value.
    """
    now = time.monotonic()

    # Clean up expired items lazily to avoid memory leaks
    expired_keys = [k for k, (exp, _) in _memory_cache.items() if now > exp]
    for k in expired_keys:
        del _memory_cache[k]

    if key in _memory_cache:
        expiry, val = _memory_cache[key]
        if now <= expiry:
            logger.debug("cache_hit", key=key)
            return val
        else:
            del _memory_cache[key]

    logger.debug("cache_miss", key=key)
    result = await fetch_fn()
    _memory_cache[key] = (now + ttl, result)
    return result
