# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Publish Wildlife Watcher media to a user's iNaturalist account.

Consolidates selected media into temporal-burst observations (one iNat
observation per encounter), filters by-catch (human/vehicle/blank), uploads each
photo through the backend proxy, and records the mapping in inat_observations /
inat_observation_media for status tracking + community-ID sync.

Pure orchestration over the lower-level helpers in ``domain/inaturalist.py``.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

import structlog

from app.domain.inaturalist import (
    INatDomainError,
    create_observation,
    upload_observation_photo,
)
from app.domain.media_resolver import resolve_media
from app.services.db_utils import rows_of
from app.services.inat_oauth import get_user_token
from app.services.supabase_client import create_service_client

logger = structlog.get_logger()

BYCATCH_TYPES = {"human", "vehicle", "blank"}
_HUMAN_REVIEWED = {"human_reviewed", "expert_reviewed", "consensus_approved"}


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


def _best_species(burst: List[dict]) -> Optional[str]:
    """Most-common animal scientific name in a burst (human-reviewed preferred)."""
    names: List[str] = []
    for m in burst:
        human = [o for o in m["_animal_obs"] if o.get("source_type") == "human" or o.get("review_status") in _HUMAN_REVIEWED]
        for o in human or m["_animal_obs"]:
            if o.get("scientific_name"):
                names.append(o["scientific_name"])
    if not names:
        return None
    return Counter(names).most_common(1)[0][0]


# ── Orchestrator ─────────────────────────────────────────────────────


async def publish_media_to_inat(
    user_id: str,
    media_ids: List[str],
    gap_seconds: int = 60,
    geoprivacy: str = "obscured",
) -> Dict[str, Any]:
    """Consolidate selected WW media into iNaturalist observations and upload them.

    - Filters out by-catch (human/vehicle/blank) and media with no animal label.
    - Skips media already published (idempotent via inat_observation_media).
    - Clusters the rest per deployment into temporal bursts; each burst becomes
      one iNat observation carrying all its photos.
    - Records the mapping in inat_observations + inat_observation_media.
    """
    token = await get_user_token(user_id)
    if not token:
        raise INatDomainError("Not connected to iNaturalist")

    svc = create_service_client()

    def _load():
        media = rows_of(
            svc.table("media").select("id, deployment_id, file_path, file_name, timestamp").in_("id", media_ids).is_("deleted_at", "null").execute()
        )
        obs = rows_of(
            svc.table("observations")
            .select("media_id, observation_type, scientific_name, vernacular_name, review_status, source_type")
            .in_("media_id", media_ids)
            .is_("deleted_at", "null")
            .execute()
        )
        dep_ids = sorted({m["deployment_id"] for m in media})
        deps = (
            rows_of(svc.table("deployments").select("id, name, location_name, latitude, longitude").in_("id", dep_ids).execute()) if dep_ids else []
        )
        published = rows_of(svc.table("inat_observation_media").select("media_id").in_("media_id", media_ids).execute())
        return media, obs, deps, {p["media_id"] for p in published}

    media, observations, deployments, already = await asyncio.to_thread(_load)
    dep_by_id = {d["id"]: d for d in deployments}

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
    }

    # By-catch filter: keep only media with at least one animal observation.
    keep: List[dict] = []
    for m in media:
        if m["id"] in already:
            result["skipped_already_published"] += 1
            continue
        animal = [o for o in obs_by_media.get(m["id"], []) if o.get("observation_type") == "animal"]
        if not animal:
            result["skipped_bycatch"] += 1
            continue
        m["_animal_obs"] = animal
        keep.append(m)

    by_dep: Dict[str, List[dict]] = {}
    for m in keep:
        by_dep.setdefault(m["deployment_id"], []).append(m)

    for dep_id, dep_media in by_dep.items():
        dep = dep_by_id.get(dep_id, {})
        if dep.get("latitude") is None or dep.get("longitude") is None:
            logger.warning("inat_publish_no_coords", deployment=dep_id)
            result["errors"] += 1
            continue
        for burst in _cluster_bursts(dep_media, gap_seconds):
            await _publish_one_burst(svc, user_id, dep, burst, geoprivacy, result)

    logger.info(
        "inat_publish_complete",
        user_id=user_id,
        **{k: v for k, v in result.items() if k != "observations"},
    )
    return result


async def _publish_one_burst(svc, user_id, dep, burst, geoprivacy, result) -> None:
    """Create one iNat observation for a burst and upload its photos."""
    species = _best_species(burst)
    times = [t for m in burst if (t := _parse_ts(m.get("timestamp"))) is not None]
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
        return

    inat_id = obs.get("id")
    if inat_id is None:
        # Without an id there is nothing to attach photos to or to sync later.
        logger.error("inat_publish_create_no_id", deployment=dep.get("id"))
        result["errors"] += 1
        return
    inat_uuid = obs.get("uuid")
    inat_uri = obs.get("uri") or (f"https://www.inaturalist.org/observations/{inat_id}" if inat_id else None)

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
        return
    if not row:
        logger.error("inat_publish_db_insert_empty", deployment=dep.get("id"), inat_observation_id=inat_id)
        result["errors"] += 1
        return
    result["observations_created"] += 1

    photo_errors = 0
    for m in burst:
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
                        "inat_observation_id": row["id"],
                        "media_id": m["id"],
                        "inat_photo_id": photo.get("id") if isinstance(photo, dict) else None,
                        "original_filename": m["id"],
                    }
                ).execute()

            await asyncio.to_thread(_link)
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
            ).eq("id", row["id"]).execute()

        await asyncio.to_thread(_mark)

    result["observations"].append(
        {
            "inat_observation_id": inat_id,
            "uri": inat_uri,
            "species_guess": species,
            "media_count": len(burst),
            "photo_errors": photo_errors,
        }
    )
