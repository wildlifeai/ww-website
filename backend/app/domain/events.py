# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Temporal event clustering and effort computation — pure domain logic.

Groups per-image observations into ecologically independent events
using a configurable temporal gap, then computes effort statistics
(trap nights, false trigger rate) for each deployment.

No HTTP or FastAPI imports — this module runs in the domain layer.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import structlog
from postgrest import CountMethod

from app.schemas.pipeline import (
    ClusterEventsResult,
    DeploymentEffortSummary,
    ObservationEventSummary,
)
from app.services.db_utils import row_of, rows_of
from app.services.supabase_client import create_service_client

logger = structlog.get_logger()


# ── Temporal Clustering ──────────────────────────────────────────────


def _group_observations_by_species(
    observations: list[dict],
) -> dict[Optional[str], list[dict]]:
    """Group observations by taxon_id (None key for unclassified)."""
    groups: dict[Optional[str], list[dict]] = defaultdict(list)
    for obs in observations:
        taxon_key = obs.get("taxon_id")
        groups[taxon_key].append(obs)
    return groups


def _cluster_temporal(
    observations: list[dict],
    gap_minutes: int,
) -> list[list[dict]]:
    """Split a sorted list of observations into clusters by temporal gap.

    Each cluster contains observations where consecutive timestamps are
    within `gap_minutes` of each other.
    """
    if not observations:
        return []

    # Sort by timestamp (from media join)
    sorted_obs = sorted(
        observations,
        key=lambda o: o.get("media_timestamp") or o.get("created_at") or "",
    )

    gap_seconds = gap_minutes * 60
    clusters: list[list[dict]] = [[sorted_obs[0]]]

    for obs in sorted_obs[1:]:
        prev_ts = clusters[-1][-1].get("media_timestamp") or clusters[-1][-1].get("created_at")
        curr_ts = obs.get("media_timestamp") or obs.get("created_at")

        if prev_ts and curr_ts:
            try:
                t_prev = datetime.fromisoformat(prev_ts.replace("Z", "+00:00"))
                t_curr = datetime.fromisoformat(curr_ts.replace("Z", "+00:00"))
                delta = abs((t_curr - t_prev).total_seconds())
                if delta > gap_seconds:
                    clusters.append([])
            except (ValueError, TypeError):
                pass

        clusters[-1].append(obs)

    return clusters


def _build_event_row(
    cluster: list[dict],
    deployment_id: str,
    taxon_id: Optional[str],
    user_id: Optional[str] = None,
) -> dict:
    """Build an observation_events INSERT row from a temporal cluster."""
    timestamps = []
    for obs in cluster:
        ts_str = obs.get("media_timestamp") or obs.get("created_at")
        if ts_str:
            try:
                timestamps.append(datetime.fromisoformat(ts_str.replace("Z", "+00:00")))
            except (ValueError, TypeError):
                pass

    if not timestamps:
        now = datetime.now(timezone.utc)
        timestamps = [now]

    start = min(timestamps)
    end = max(timestamps)
    duration = int((end - start).total_seconds())

    # Pick the highest-confidence observation as the primary media
    best = max(cluster, key=lambda o: o.get("confidence") or 0.0)
    primary_media = best.get("media_id")
    avg_confidence = None
    confidences = [c for o in cluster if (c := o.get("confidence")) is not None]
    if confidences:
        avg_confidence = sum(confidences) / len(confidences)

    return {
        "id": str(uuid.uuid4()),
        "deployment_id": deployment_id,
        "taxon_id": taxon_id,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "event_duration_seconds": duration,
        "media_count": len(cluster),
        "primary_media_id": primary_media,
        "review_status": "ai_reviewed",
        "confidence": avg_confidence,
        "created_by": user_id,
    }


async def cluster_deployment_events(
    deployment_id: str,
    gap_minutes: int = 30,
    min_images: int = 1,
    user_id: Optional[str] = None,
) -> ClusterEventsResult:
    """Cluster all observations for a deployment into temporal events.

    Steps:
    1. Fetch observations + media timestamps for the deployment.
    2. Group by taxon_id.
    3. Split each group into temporal clusters using `gap_minutes`.
    4. Insert observation_events and link observations.
    5. Record an annotation_run for provenance.

    Args:
        deployment_id: UUID of the deployment.
        gap_minutes: Temporal gap threshold in minutes.
        min_images: Minimum images per event (clusters below are discarded).
        user_id: UUID of the user triggering the clustering.

    Returns:
        ClusterEventsResult with created events and linkage counts.
    """
    svc = create_service_client()

    # 1. Fetch observations with media timestamps
    def _fetch():
        resp = (
            svc.table("observations")
            .select("id, deployment_id, media_id, taxon_id, scientific_name, confidence, created_at, media!inner(timestamp)")
            .eq("deployment_id", deployment_id)
            .is_("deleted_at", "null")
            .order("created_at")
            .execute()
        )
        return rows_of(resp)

    raw_obs = await asyncio.to_thread(_fetch)

    # Flatten media timestamp into each observation dict
    observations = []
    for row in raw_obs:
        flat = {k: v for k, v in row.items() if k != "media"}
        media_data = row.get("media")
        if isinstance(media_data, dict):
            flat["media_timestamp"] = media_data.get("timestamp")
        observations.append(flat)

    if not observations:
        logger.info("events_clustering_empty", deployment_id=deployment_id)
        return ClusterEventsResult(deployment_id=deployment_id)

    # 2. Group by species
    species_groups = _group_observations_by_species(observations)

    # 3. Cluster each species group temporally
    all_events: list[dict] = []
    obs_to_event: dict[str, str] = {}  # observation_id → event_id

    for taxon_id, obs_list in species_groups.items():
        clusters = _cluster_temporal(obs_list, gap_minutes)
        for cluster in clusters:
            if len(cluster) < min_images:
                continue
            event_row = _build_event_row(cluster, deployment_id, taxon_id, user_id)
            all_events.append(event_row)
            for obs in cluster:
                obs_to_event[obs["id"]] = event_row["id"]

    if not all_events:
        logger.info("events_clustering_no_events", deployment_id=deployment_id)
        return ClusterEventsResult(deployment_id=deployment_id)

    # 4. Clear existing events for this deployment, then insert new ones
    def _write_events():
        # Delete existing events (cascade will unlink observations)
        svc.table("observation_events").delete().eq("deployment_id", deployment_id).execute()

        # Bulk insert new events (chunked)
        chunk_size = 50
        for i in range(0, len(all_events), chunk_size):
            batch = all_events[i : i + chunk_size]
            svc.table("observation_events").insert(batch).execute()

        # Link observations to their events — grouped so each event is one bulk
        # UPDATE rather than one query per observation.
        from collections import defaultdict

        event_to_obs: dict[str, list[str]] = defaultdict(list)
        for obs_id, event_id in obs_to_event.items():
            event_to_obs[event_id].append(obs_id)
        for event_id, obs_ids in event_to_obs.items():
            svc.table("observations").update({"observation_event_id": event_id}).in_("id", obs_ids).execute()

    await asyncio.to_thread(_write_events)

    # 5. Record annotation run for provenance
    def _record_run():
        svc.table("annotation_runs").insert(
            {
                "deployment_id": deployment_id,
                "run_type": "event_aggregation",
                "config": {"gap_minutes": gap_minutes, "min_images": min_images},
                "observation_count": len(obs_to_event),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "created_by": user_id,
            }
        ).execute()

    await asyncio.to_thread(_record_run)

    # 6. Build summaries
    event_summaries = []
    for ev in all_events:
        event_summaries.append(
            ObservationEventSummary(
                id=ev["id"],
                deployment_id=ev["deployment_id"],
                taxon_id=ev.get("taxon_id"),
                start_time=ev["start_time"],
                end_time=ev["end_time"],
                event_duration_seconds=ev["event_duration_seconds"],
                media_count=ev["media_count"],
                review_status=ev.get("review_status", "unreviewed"),
                confidence=ev.get("confidence"),
            )
        )

    logger.info(
        "events_clustering_complete",
        deployment_id=deployment_id,
        events_created=len(all_events),
        observations_linked=len(obs_to_event),
    )

    return ClusterEventsResult(
        deployment_id=deployment_id,
        events_created=len(all_events),
        observations_linked=len(obs_to_event),
        events=event_summaries,
    )


# ── Effort Computation ──────────────────────────────────────────────


async def compute_deployment_effort(
    deployment_id: str,
) -> DeploymentEffortSummary:
    """Compute and upsert effort statistics for a single deployment.

    Calculates:
    - trap_nights: days between deployment_start and deployment_end (or now)
    - camera_uptime_hours: trap_nights * 24 (assuming continuous operation)
    - total_events: count of observation_events
    - total_media: count of media rows
    - false_trigger_rate: blank/unknown events / total_events

    Returns:
        DeploymentEffortSummary with the computed values.
    """
    svc = create_service_client()

    def _compute():
        # Fetch deployment dates
        dep = svc.table("deployments").select("deployment_start, deployment_end").eq("id", deployment_id).single().execute()
        dep_data = row_of(dep) or {}

        start_str = dep_data.get("deployment_start")
        end_str = dep_data.get("deployment_end")
        now = datetime.now(timezone.utc)

        trap_nights = 0.0
        if start_str:
            try:
                t_start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                t_end = datetime.fromisoformat(end_str.replace("Z", "+00:00")) if end_str else now
                trap_nights = max(0.0, (t_end - t_start).total_seconds() / 86400)
            except (ValueError, TypeError):
                pass

        uptime_hours = trap_nights * 24

        # Count events
        events_resp = (
            svc.table("observation_events")
            .select("id, trigger_type", count=CountMethod.exact)
            .eq("deployment_id", deployment_id)
            .is_("deleted_at", "null")
            .execute()
        )
        total_events = events_resp.count or 0
        false_triggers = sum(1 for e in rows_of(events_resp) if e.get("trigger_type") in ("wind", "rain", "lighting", "vegetation"))
        false_rate = false_triggers / total_events if total_events > 0 else 0.0

        # Count media
        media_resp = svc.table("media").select("id", count=CountMethod.exact).eq("deployment_id", deployment_id).execute()
        total_media = media_resp.count or 0

        # Upsert effort row
        now_iso = now.isoformat()
        svc.table("deployment_effort").upsert(
            {
                "deployment_id": deployment_id,
                "trap_nights": round(trap_nights, 2),
                "camera_uptime_hours": round(uptime_hours, 2),
                "total_events": total_events,
                "total_media": total_media,
                "false_trigger_rate": round(false_rate, 4),
                "computed_at": now_iso,
            }
        ).execute()

        return DeploymentEffortSummary(
            deployment_id=deployment_id,
            trap_nights=round(trap_nights, 2),
            camera_uptime_hours=round(uptime_hours, 2),
            total_events=total_events,
            total_media=total_media,
            false_trigger_rate=round(false_rate, 4),
            computed_at=now,
        )

    return await asyncio.to_thread(_compute)
