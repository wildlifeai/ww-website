# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sync community identifications from iNaturalist back into Wildlife Watcher.

Polls the public iNat API for the current taxon + quality_grade of observations
we published (tracked in inat_observations), then:
  1. Updates the inat_observations mapping (sync_status, quality_grade,
     community_taxon, last_synced_at) — this drives the thumbnail iNat badge.
  2. Writes the community taxon back into WW `observations` as a
     source_type='consensus' record on each photo in the burst (idempotent),
     so the community ID flows into the science data.

Reconciliation uses the stored mapping (inat_observation_id -> our row -> media
via inat_observation_media), so no scraping or original_filename guesswork is
needed. Runs with the service role (bypasses RLS) — intended to be triggered by
POST /api/inat/sync or a scheduled job.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog

from app.domain.inaturalist import batch_poll_observations
from app.services.db_utils import rows_of
from app.services.supabase_client import create_service_client

logger = structlog.get_logger()

_CONSENSUS_BY = "iNaturalist community"
_OPEN_STATES = ["uploaded", "needs_id", "research", "disagreement"]


def _derive_status(quality_grade: Optional[str], community: Optional[str], guess: Optional[str]) -> str:
    """Map iNat quality_grade + community taxon (vs our guess) → badge state."""
    if quality_grade == "research":
        if community and guess and community.strip().lower() != guess.strip().lower():
            return "disagreement"
        return "research"
    if quality_grade == "needs_id":
        return "needs_id"
    return "uploaded"  # casual / unknown — still on iNat, no consensus yet


async def sync_inat_identifications(user_id: Optional[str] = None, limit: int = 200) -> Dict[str, Any]:
    """Poll iNat for community IDs and reconcile them into Wildlife Watcher.

    Args:
        user_id: limit to one user's published observations (None = all users).
        limit: max iNat observations to poll in this run (batch cap is 200).
    """
    svc = create_service_client()

    def _load_rows():
        q = (
            svc.table("inat_observations")
            .select("id, deployment_id, user_id, inat_observation_id, species_guess, sync_status")
            .not_.is_("inat_observation_id", "null")
            .in_("sync_status", _OPEN_STATES)
        )
        if user_id:
            q = q.eq("user_id", user_id)
        return rows_of(q.limit(limit).execute())

    rows = await asyncio.to_thread(_load_rows)
    result: Dict[str, Any] = {
        "checked": len(rows),
        "updated": 0,
        "research": 0,
        "disagreement": 0,
        "observations_written": 0,
    }
    if not rows:
        return result

    inat_ids = [r["inat_observation_id"] for r in rows if r.get("inat_observation_id")]
    polled = await batch_poll_observations(inat_ids)
    poll_by_id = {p["id"]: p for p in polled}
    now = datetime.now(timezone.utc).isoformat()

    for r in rows:
        p = poll_by_id.get(r["inat_observation_id"])
        if not p:
            continue
        quality = p.get("quality_grade")
        community = p.get("community_taxon") or None
        new_status = _derive_status(quality, community, r.get("species_guess"))

        def _update(r=r, new_status=new_status, quality=quality, community=community):
            svc.table("inat_observations").update(
                {
                    "sync_status": new_status,
                    "quality_grade": quality,
                    "community_taxon": community,
                    "last_synced_at": now,
                }
            ).eq("id", r["id"]).execute()

        await asyncio.to_thread(_update)
        result["updated"] += 1
        if new_status == "research":
            result["research"] += 1
        elif new_status == "disagreement":
            result["disagreement"] += 1

        if community:
            result["observations_written"] += await _write_consensus(
                svc,
                r,
                community,
                research=(quality == "research"),
                now=now,
            )

    logger.info("inat_sync_complete", user_id=user_id, **result)
    return result


async def _write_consensus(svc, row, community: str, research: bool, now: str) -> int:
    """Write/refresh a consensus observation per photo in the burst (idempotent)."""

    def _media_ids() -> List[str]:
        data = svc.table("inat_observation_media").select("media_id").eq("inat_observation_id", row["id"]).execute().data or []
        return [d["media_id"] for d in data]

    def _resolve_taxon() -> Optional[str]:
        data = svc.table("taxa").select("id").ilike("scientific_name", community).limit(1).execute().data or []
        return data[0]["id"] if data else None

    media_ids, taxon_id = await asyncio.to_thread(lambda: (_media_ids(), _resolve_taxon()))
    written = 0
    review_status = "consensus_approved" if research else "ai_reviewed"

    for media_id in media_ids:

        def _upsert(media_id=media_id):
            existing = (
                svc.table("observations")
                .select("id")
                .eq("media_id", media_id)
                .eq("source_type", "consensus")
                .eq("classified_by", _CONSENSUS_BY)
                .is_("deleted_at", "null")
                .limit(1)
                .execute()
                .data
                or []
            )
            payload = {
                "deployment_id": row["deployment_id"],
                "media_id": media_id,
                "observation_level": "media",
                "observation_type": "animal",
                "source_type": "consensus",
                "review_status": review_status,
                "classified_by": _CONSENSUS_BY,
                "classification_timestamp": now,
                "scientific_name": community,
                "taxon_id": taxon_id,
            }
            if existing:
                svc.table("observations").update(payload).eq("id", existing[0]["id"]).execute()
                return 0
            svc.table("observations").insert(payload).execute()
            return 1

        written += await asyncio.to_thread(_upsert)

    return written
