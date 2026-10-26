# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Publish Wildlife Watcher media to a user's iNaturalist account.

Consolidates selected media into temporal-burst observations (one iNat
observation per encounter), filters by-catch (see _photo_label), uploads each
photo through the backend proxy, and records the mapping in inat_observations /
inat_observation_media for status tracking + community-ID sync.

Pure orchestration over the lower-level helpers in ``domain/inaturalist.py``.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import structlog

from app.domain.inaturalist import (
    INatDomainError,
    create_observation,
    upload_observation_photo,
)
from app.domain.media_resolver import resolve_media
from app.domain.public_api import photo_verdict
from app.services.db_utils import read_all, rows_of
from app.services.inat_oauth import get_user_token
from app.services.supabase_client import create_service_client

logger = structlog.get_logger()

# What photo_verdict reads, and the order it expects (the order the rows were written).
_OBS_COLUMNS = "id, media_id, observation_type, scientific_name, vernacular_name, review_status, source_type, classification_method, created_at"


# ── Helpers ──────────────────────────────────────────────────────────


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _cluster_bursts(media: List[dict], gap_seconds: int) -> List[List[dict]]:
    """Group media from one deployment into temporal bursts (Δt < gap_seconds)."""
    timed = sorted(
        ((t, m) for m in media if (t := _parse_ts(m.get("timestamp"))) is not None),
        key=lambda tm: tm[0],
    )
    untimed = [m for m in media if not _parse_ts(m.get("timestamp"))]

    bursts: List[List[dict]] = []
    cur: List[dict] = []
    prev: Optional[datetime] = None
    for t, m in timed:
        if prev is not None and (t - prev).total_seconds() >= gap_seconds:
            bursts.append(cur)
            cur = []
        cur.append(m)
        prev = t
    if cur:
        bursts.append(cur)

    # Each untimed image is its own observation (cannot be clustered).
    bursts.extend([m] for m in untimed)
    return bursts


def _photo_label(obs: List[dict]) -> Optional[dict]:
    """The label a photo goes to iNaturalist under, or None when it stays off.

    The label is the one the photo's card shows (``public_api.photo_verdict``), so a reviewer's
    Empty or person outvotes the AI. The photo stays off unless that label is an animal, and
    whenever any row, a model's or a person's, says a person is in it.
    """
    if any(o.get("observation_type") == "human" for o in obs):
        return None
    label, is_empty = photo_verdict(obs)
    if is_empty or label is None or label.get("observation_type") != "animal":
        return None
    return label


def _best_species(burst: List[dict]) -> Optional[str]:
    """Most-common scientific name in a burst, each photo by its own label (see _photo_label)."""
    names = [m["_label"]["scientific_name"] for m in burst if m["_label"].get("scientific_name")]
    if not names:
        return None
    return Counter(names).most_common(1)[0][0]


# ── Orchestrator ─────────────────────────────────────────────────────

# A request stops starting photos after this many seconds and hands the rest back
# (``remaining_media_ids``) for the caller to send again. Container Apps ends a request at
# about 240 s and one photo upload can take a minute, so this leaves room for the last one.
PUBLISH_BUDGET_SECONDS = 120.0


def _one(embed: Any) -> Optional[dict]:
    """A to-one embed, which PostgREST may return as a one-row list."""
    if isinstance(embed, list):
        return embed[0] if embed else None
    return embed if isinstance(embed, dict) else None


async def publish_media_to_inat(
    user_id: str,
    media_ids: List[str],
    gap_seconds: int = 60,
    geoprivacy: str = "obscured",
    budget_seconds: float = PUBLISH_BUDGET_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> Dict[str, Any]:
    """Consolidate selected WW media into iNaturalist observations and upload them.

    - Filters out by-catch: a photo whose label is not an animal, or that shows a person.
    - Skips media already published (idempotent via inat_observation_media).
    - Clusters the rest per deployment into temporal bursts; each burst becomes
      one iNat observation carrying all its photos. A burst that holds a photo this
      user already published joins that photo's observation instead of starting one.
    - Records the mapping in inat_observations + inat_observation_media.
    - Starts no photo once ``budget_seconds`` have passed (after the first) and returns
      the photos it did not reach as ``remaining_media_ids``, with the published photos
      of a burst it stopped part way through, so that sending them again carries on.
    """
    token = await get_user_token(user_id)
    if not token:
        raise INatDomainError("Not connected to iNaturalist")

    deadline = clock() + budget_seconds
    svc = create_service_client()

    def _load():
        media = rows_of(
            svc.table("media").select("id, deployment_id, file_path, file_name, timestamp").in_("id", media_ids).is_("deleted_at", "null").execute()
        )
        # 200 photos can carry more than 1,000 rows (one per model, a consensus, a person's).
        obs = read_all(
            lambda: (
                svc.table("observations").select(_OBS_COLUMNS).in_("media_id", media_ids).is_("deleted_at", "null").order("created_at").order("id")
            )
        )
        dep_ids = sorted({m["deployment_id"] for m in media})
        deps = (
            rows_of(svc.table("deployments").select("id, name, location_name, latitude, longitude").in_("id", dep_ids).execute()) if dep_ids else []
        )
        links = rows_of(
            svc.table("inat_observation_media")
            .select("media_id, inat_observation_id, inat_observations(user_id, inat_observation_id, inat_uri, species_guess)")
            .in_("media_id", media_ids)
            .execute()
        )
        return media, obs, deps, links

    media, observations, deployments, links = await asyncio.to_thread(_load)
    dep_by_id = {d["id"]: d for d in deployments}
    already = {link["media_id"] for link in links}
    # This user's published photos: the iNat observation each one's burst-mates join.
    anchor_of: Dict[str, dict] = {}
    for link in links:
        io = _one(link.get("inat_observations"))
        if io and io.get("user_id") == user_id and io.get("inat_observation_id") is not None:
            anchor_of.setdefault(link["media_id"], {**io, "row_id": link["inat_observation_id"]})

    obs_by_media: Dict[str, List[dict]] = {}
    for o in observations:
        obs_by_media.setdefault(o["media_id"], []).append(o)

    result: Dict[str, Any] = {
        "observations_created": 0,
        "photos_uploaded": 0,
        "skipped_bycatch": 0,
        "skipped_already_published": 0,
        "errors": 0,
        "observations": [],
        "remaining_media_ids": [],
    }

    # By-catch filter: keep only media whose label (see _photo_label) is an animal.
    keep: List[dict] = []
    anchors: List[dict] = []
    for m in media:
        if m["id"] in already:
            result["skipped_already_published"] += 1
            if m["id"] in anchor_of:
                m["_anchor"] = anchor_of[m["id"]]
                anchors.append(m)
            continue
        label = _photo_label(obs_by_media.get(m["id"], []))
        if label is None:
            result["skipped_bycatch"] += 1
            continue
        m["_label"] = label
        keep.append(m)

    by_dep: Dict[str, List[dict]] = {}
    for m in keep:
        by_dep.setdefault(m["deployment_id"], []).append(m)
    for m in anchors:
        if m["deployment_id"] in by_dep:
            by_dep[m["deployment_id"]].append(m)

    work: List[tuple] = []
    for dep_id, dep_media in by_dep.items():
        dep = dep_by_id.get(dep_id, {})
        if dep.get("latitude") is None or dep.get("longitude") is None:
            logger.warning("inat_publish_no_coords", deployment=dep_id)
            result["errors"] += 1
            continue
        work.extend((dep, burst) for burst in _cluster_bursts(dep_media, gap_seconds) if any("_label" in m for m in burst))

    def time_up() -> bool:
        return clock() >= deadline

    for i, (dep, burst) in enumerate(work):
        if i > 0 and time_up():
            result["remaining_media_ids"] = [m["id"] for _, b in work[i:] for m in b]
            break
        handed_back = await _publish_one_burst(svc, user_id, dep, burst, geoprivacy, result, time_up)
        if handed_back:
            result["remaining_media_ids"] = handed_back + [m["id"] for _, b in work[i + 1 :] for m in b]
            break

    logger.info(
        "inat_publish_complete",
        user_id=user_id,
        remaining=len(result["remaining_media_ids"]),
        **{k: v for k, v in result.items() if k not in ("observations", "remaining_media_ids")},
    )
    return result


async def _create_inat_observation(svc, user_id, dep, photos, geoprivacy, result) -> Optional[Dict[str, Any]]:
    """Create the iNat observation for a burst and record it; None (an error counted) on failure."""
    species = _best_species(photos)
    times = [t for m in photos if (t := _parse_ts(m.get("timestamp"))) is not None]
    observed_on = min(times).date().isoformat() if times else ""
    site = dep.get("location_name") or dep.get("name") or "a Wildlife Watcher deployment"

    try:
        obs = await create_observation(
            user_id=user_id,
            species_guess=species or "",
            latitude=dep["latitude"],
            longitude=dep["longitude"],
            observed_on=observed_on,
            description=f"Camera-trap encounter from {site}, uploaded via Wildlife Watcher.",
            geoprivacy=geoprivacy,
        )
    except INatDomainError as exc:
        logger.error("inat_publish_create_failed", deployment=dep.get("id"), error=str(exc))
        result["errors"] += 1
        return None

    inat_id = obs.get("id")
    if inat_id is None:
        # Without an id there is nothing to attach photos to or to sync later.
        logger.error("inat_publish_create_no_id", deployment=dep.get("id"))
        result["errors"] += 1
        return None
    inat_uuid = obs.get("uuid")
    inat_uri = obs.get("uri") or f"https://www.inaturalist.org/observations/{inat_id}"

    def _insert_obs():
        resp = (
            svc.table("inat_observations")
            .insert(
                {
                    "deployment_id": dep["id"],
                    "user_id": user_id,
                    "inat_observation_id": inat_id,
                    "inat_uuid": str(inat_uuid) if inat_uuid else None,
                    "inat_uri": inat_uri,
                    "species_guess": species,
                    "geoprivacy": geoprivacy,
                    "sync_status": "uploaded",
                }
            )
            .execute()
        )
        return rows_of(resp)[0] if resp.data else None

    # The iNat observation is already created remotely at this point. If recording
    # it locally fails, isolate the error (count it, skip this burst's photos) rather
    # than letting the exception abort the whole batch and orphan more remote obs.
    try:
        row = await asyncio.to_thread(_insert_obs)
    except Exception as exc:  # noqa: BLE001 — per-burst isolation
        logger.error("inat_publish_db_insert_failed", deployment=dep.get("id"), inat_observation_id=inat_id, error=str(exc))
        result["errors"] += 1
        return None
    if not row:
        logger.error("inat_publish_db_insert_empty", deployment=dep.get("id"), inat_observation_id=inat_id)
        result["errors"] += 1
        return None
    result["observations_created"] += 1
    return {"row_id": row["id"], "inat_observation_id": inat_id, "inat_uri": inat_uri, "species_guess": species}


async def _publish_one_burst(svc, user_id, dep, burst, geoprivacy, result, time_up: Callable[[], bool] = lambda: False) -> List[str]:
    """Upload a burst's photos to one iNat observation: the one a published photo of the burst
    is in (``_anchor``), else a new one.

    Stops before a photo once ``time_up()`` (never before the first) and returns the burst's
    photos to send again: the ones on iNaturalist, so the rest join their observation, and the
    ones not tried. A failed photo is not sent again. Returns [] when it got through.
    """
    photos = [m for m in burst if "_label" in m]
    target = next((m["_anchor"] for m in burst if "_anchor" in m), None)
    if target is None:
        target = await _create_inat_observation(svc, user_id, dep, photos, geoprivacy, result)
        if target is None:
            return []
    row_id, inat_id = target["row_id"], target["inat_observation_id"]

    on_inat = [m["id"] for m in burst if "_anchor" in m]
    photo_errors = 0
    tried = 0
    for m in photos:
        if tried and time_up():
            break
        tried += 1
        try:
            resolved = await resolve_media(m["file_path"], size="full")
            if not resolved:
                photo_errors += 1
                continue
            data, _ct = resolved
            photo = await upload_observation_photo(
                user_id=user_id,
                observation_id=inat_id,
                photo_bytes=data,
                filename=m["id"],  # WW media id as original_filename for sync reconciliation
            )

            def _link(photo=photo, m=m):
                svc.table("inat_observation_media").insert(
                    {
                        "inat_observation_id": row_id,
                        "media_id": m["id"],
                        "inat_photo_id": photo.get("id") if isinstance(photo, dict) else None,
                        "original_filename": m["id"],
                    }
                ).execute()

            await asyncio.to_thread(_link)
            on_inat.append(m["id"])
            result["photos_uploaded"] += 1
        except Exception as exc:  # noqa: BLE001 — per-photo isolation
            logger.warning("inat_publish_photo_failed", media_id=m.get("id"), error=str(exc))
            photo_errors += 1

    if photo_errors:

        def _mark():
            svc.table("inat_observations").update(
                {
                    "sync_status": "failed",
                    "error_message": f"{photo_errors} photo upload(s) failed",
                }
            ).eq("id", row_id).execute()

        await asyncio.to_thread(_mark)

    result["observations"].append(
        {
            "inat_observation_id": inat_id,
            "uri": target.get("inat_uri"),
            "species_guess": target.get("species_guess"),
            "media_count": tried,
            "photo_errors": photo_errors,
        }
    )
    return on_inat + [m["id"] for m in photos[tried:]] if tried < len(photos) else []
