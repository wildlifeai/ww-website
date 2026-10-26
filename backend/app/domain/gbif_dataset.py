# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""A project's GBIF dataset (#373): the key a project admin saves in project settings, and the
GBIF figures of the GBF report's Target 21, open data.

``projects.gbif_dataset_key`` (ww-backend#343) holds the key, never the DOI: the GBIF API takes
the key and returns the DOI. A project admin saves it as themselves, under
``projects_update_policy``, once GBIF knows the dataset.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlparse

from app.services import gbif
from app.services.gbif import GbifNotFound, GbifUnavailable

__all__ = ["GbifNotFound", "GbifUnavailable", "dataset_summary", "parse_dataset_key", "save_dataset_key_as_user", "with_gbif_figures"]

Row = Dict[str, Any]

_KEY = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
_GBIF_HOSTS = {"gbif.org", "www.gbif.org", "api.gbif.org"}


def parse_dataset_key(text: str) -> Optional[str]:
    """The dataset key in a bare key or a gbif.org dataset link (``www.gbif.org/dataset/<key>``,
    with or without a page after it, or the API's ``/v1/dataset/<key>``), in lower case. None when
    there is none."""
    text = text.strip()
    if _KEY.fullmatch(text):
        return text.lower()
    url = urlparse(text if "://" in text else f"https://{text}")
    if (url.hostname or "").lower() not in _GBIF_HOSTS:
        return None
    parts = [p for p in url.path.split("/") if p]
    for i, part in enumerate(parts[:-1]):
        if part == "dataset" and _KEY.fullmatch(parts[i + 1]):
            return parts[i + 1].lower()
    return None


async def dataset_summary(key: str) -> Row:
    """What GBIF says of the dataset: title, DOI, published occurrences and the date it was last
    published (``pubDate``, else ``modified``). Raises ``GbifNotFound`` or ``GbifUnavailable``."""
    meta, records = await asyncio.gather(gbif.dataset(key), gbif.occurrence_count(key))
    published = str(meta.get("pubDate") or meta.get("modified") or "")[:10]
    return {
        "dataset_key": key,
        "title": meta.get("title"),
        "doi": meta.get("doi"),
        "published_records": records,
        "last_published": published or None,
    }


async def with_gbif_figures(datasets: List[Row]) -> List[Row]:
    """Target 21's rows, one per project, with GBIF's figures added and a ``status``:
    ``published``, ``not_published`` (no key), ``not_found`` (GBIF no longer has the dataset) or
    ``unavailable`` (GBIF did not answer). GBIF failing never fails the report."""

    async def one(row: Row) -> Row:
        out: Row = {**row, "status": "not_published", "title": None, "doi": None, "published_records": None, "last_published": None}
        key = row.get("dataset_key")
        if not key:
            return out
        try:
            out.update(await dataset_summary(str(key)), status="published")
        except GbifNotFound:
            out["status"] = "not_found"
        except GbifUnavailable:
            out["status"] = "unavailable"
        return out

    return list(await asyncio.gather(*(one(r) for r in datasets)))


def save_dataset_key_as_user(user_client, project_id: str, key: Optional[str]) -> Literal["saved", "refused", "not_found"]:
    """Set (or, with None, clear) the project's dataset key as the caller.

    ``projects_update_policy`` lets a project admin of the project or ``ww_admin`` update it and
    turns anyone else's update into 0 rows, so a row back means saved. Without one, a project the
    caller can still see is ``refused``, and one they cannot is ``not_found``.
    """
    saved = user_client.table("projects").update({"gbif_dataset_key": key}).eq("id", project_id).execute()
    if saved.data:
        return "saved"
    visible = user_client.table("projects").select("id").eq("id", project_id).limit(1).execute()
    return "refused" if visible.data else "not_found"
