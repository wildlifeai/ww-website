# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The public GBIF API (https://api.gbif.org/v1), read-only: a dataset's metadata and how many
occurrences it publishes (#373).

Answers are cached for a day and a call gives up after a few seconds, so a slow GBIF slows the GBF
report by that much at most. A failure is never cached.
"""

from typing import Any, Dict, Optional

import httpx

from app.services.cache import cached

GBIF_API = "https://api.gbif.org/v1"
_TIMEOUT = httpx.Timeout(5.0)
_DAY = 24 * 60 * 60

# Tests swap in an httpx.MockTransport.
transport: Optional[httpx.AsyncBaseTransport] = None


class GbifNotFound(Exception):
    """GBIF has no dataset with that key."""


class GbifUnavailable(Exception):
    """GBIF did not answer, or not with what it should."""


async def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(base_url=GBIF_API, timeout=_TIMEOUT, transport=transport) as client:
            res = await client.get(path, params=params)
    except httpx.HTTPError as exc:
        raise GbifUnavailable(str(exc)) from exc
    if res.status_code in (400, 404):  # 400: GBIF's answer to a key that is not a UUID
        raise GbifNotFound(path)
    if res.status_code != 200:
        raise GbifUnavailable(f"GBIF answered {res.status_code}")
    try:
        body = res.json()
    except ValueError as exc:
        raise GbifUnavailable("GBIF did not send JSON") from exc
    if not isinstance(body, dict):
        raise GbifUnavailable("GBIF did not send an object")
    return body


async def dataset(key: str) -> Dict[str, Any]:
    """``GET /dataset/{key}``: title, doi, pubDate and the rest."""
    return await cached(f"gbif:dataset:{key}", _DAY, lambda: _get(f"/dataset/{key}"))


async def occurrence_count(key: str) -> int:
    """How many occurrences GBIF publishes from the dataset (``/occurrence/search``, ``limit=0``)."""
    body = await cached(f"gbif:occurrences:{key}", _DAY, lambda: _get("/occurrence/search", {"datasetKey": key, "limit": 0}))
    return int(body.get("count") or 0)
