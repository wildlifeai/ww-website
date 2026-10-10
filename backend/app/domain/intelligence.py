# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Conservation intelligence — ecological change detection, dataset health, occupancy.

Cross-deployment / cross-time analytics on top of the Wildlife Brain:
- distribution shift between two time windows (Jensen-Shannon over cluster histograms),
- species occupancy overlap between deployments (Jaccard),
- species accumulation curves,
- dataset health + conservation alerts.

The math helpers are pure (stdlib only) and unit-tested; orchestration reads from
Supabase lazily. Shift detection uses **cluster-id histograms** (available in
``media_embeddings``) rather than raw vectors, so it runs without loading the vector store.
"""

from __future__ import annotations

import asyncio
import math
from typing import Optional

import structlog

from app.services.db_utils import rows_of

logger = structlog.get_logger()

SHIFT_HIGH_THRESHOLD = 0.3
SHIFT_MEDIUM_THRESHOLD = 0.15


# ── Pure analytics helpers ───────────────────────────────────────────


def _normalize(hist: dict) -> dict:
    total = sum(hist.values())
    return {k: v / total for k, v in hist.items()} if total > 0 else {}


def js_divergence(p_counts: dict, q_counts: dict) -> float:
    """Jensen-Shannon divergence (base-2, in [0, 1]) between two count histograms."""
    p, q = _normalize(p_counts), _normalize(q_counts)
    if not p or not q:
        return 0.0
    keys = set(p) | set(q)
    m = {k: 0.5 * (p.get(k, 0.0) + q.get(k, 0.0)) for k in keys}

    def _kl(a: dict) -> float:
        s = 0.0
        for k in keys:
            ak = a.get(k, 0.0)
            if ak > 0 and m.get(k, 0.0) > 0:
                s += ak * math.log2(ak / m[k])
        return s

    jsd = 0.5 * _kl(p) + 0.5 * _kl(q)
    return round(max(0.0, min(1.0, jsd)), 4)


def alert_level(divergence: float) -> str:
    """Map a divergence score to an alert level."""
    if divergence > SHIFT_HIGH_THRESHOLD:
        return "high"
    if divergence > SHIFT_MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def compare_cluster_distributions(hist_a: dict, hist_b: dict) -> list[dict]:
    """Per-cluster change between two periods, biggest absolute delta first."""
    changes: list[dict] = []
    for k in set(hist_a) | set(hist_b):
        ca, cb = hist_a.get(k, 0), hist_b.get(k, 0)
        if ca == 0 and cb > 0:
            change_type = "appeared"
        elif cb == 0 and ca > 0:
            change_type = "disappeared"
        elif cb > ca:
            change_type = "grew"
        elif cb < ca:
            change_type = "shrank"
        else:
            continue
        changes.append({"cluster_id": k, "change_type": change_type, "delta": cb - ca})
    return sorted(changes, key=lambda c: -abs(c["delta"]))


def jaccard(a: set, b: set) -> float:
    """Jaccard similarity of two sets (0 when both empty)."""
    if not a and not b:
        return 0.0
    return round(len(a & b) / len(a | b), 4)


def accumulation_curve(taxa_in_time_order: list[Optional[str]]) -> list[int]:
    """Cumulative distinct-species count after each observation (species accumulation)."""
    seen: set[str] = set()
    curve: list[int] = []
    for taxon in taxa_in_time_order:
        if taxon:
            seen.add(taxon)
        curve.append(len(seen))
    return curve


# ── Orchestration ────────────────────────────────────────────────────


def _in_window(ts: Optional[str], start: str, end: str) -> bool:
    return bool(ts) and start <= ts <= end


async def detect_distribution_shift(deployment_id: str, period_a: tuple[str, str], period_b: tuple[str, str]) -> dict:
    """Compare cluster distributions between two time windows; persist a report."""
    from app.services.supabase_client import create_service_client

    svc = create_service_client()

    def _fetch():
        resp = (
            svc.table("media")
            .select("id, timestamp, media_embeddings(cluster_id, is_outlier)")
            .eq("deployment_id", deployment_id)
            .is_("deleted_at", "null")
            .execute()
        )
        return rows_of(resp)

    rows = await asyncio.to_thread(_fetch)
    hist_a: dict[int, int] = {}
    hist_b: dict[int, int] = {}
    for r in rows:
        me = r.get("media_embeddings")
        if isinstance(me, list):
            me = me[0] if me else None
        # Skip outliers and not-yet-clustered media (cluster_id is None) — a None
        # bucket would pollute the Jensen-Shannon divergence and the changed list.
        if not me or me.get("is_outlier") or me.get("cluster_id") is None:
            continue
        cid = me.get("cluster_id")
        ts = r.get("timestamp")
        if _in_window(ts, *period_a):
            hist_a[cid] = hist_a.get(cid, 0) + 1
        elif _in_window(ts, *period_b):
            hist_b[cid] = hist_b.get(cid, 0) + 1

    divergence = js_divergence(hist_a, hist_b)
    changed = compare_cluster_distributions(hist_a, hist_b)
    level = alert_level(divergence)

    def _persist():
        svc.table("ecological_shift_reports").insert(
            {
                "deployment_id": deployment_id,
                "period_a_start": period_a[0],
                "period_a_end": period_a[1],
                "period_b_start": period_b[0],
                "period_b_end": period_b[1],
                "method": "jensen_shannon",
                "divergence": divergence,
                "alert_level": level,
                "changed_clusters": changed,
            }
        ).execute()

    await asyncio.to_thread(_persist)
    logger.info("shift_detection_complete", deployment_id=deployment_id, divergence=divergence, level=level)
    return {
        "deployment_id": deployment_id,
        "divergence": divergence,
        "alert_level": level,
        "changed_clusters": changed,
        "period_a_count": sum(hist_a.values()),
        "period_b_count": sum(hist_b.values()),
    }


async def _project_deployment_ids(project_id: str) -> list[str]:
    from app.services.supabase_client import create_service_client

    svc = create_service_client()

    def _fetch():
        resp = svc.table("deployments").select("id").eq("project_id", project_id).is_("deleted_at", "null").execute()
        return [d["id"] for d in rows_of(resp)]

    return await asyncio.to_thread(_fetch)


async def dataset_health(project_id: str) -> dict:
    """Species coverage, review funnel, and outlier rate for a project."""
    from app.services.supabase_client import create_service_client

    svc = create_service_client()
    deployment_ids = await _project_deployment_ids(project_id)
    if not deployment_ids:
        return {"project_id": project_id, "deployments": 0, "species": [], "review_funnel": {}, "outlier_rate": None}

    def _fetch():
        obs = rows_of(
            svc.table("observations")
            .select("scientific_name, review_status, observation_type")
            .in_("deployment_id", deployment_ids)
            .is_("deleted_at", "null")
            .execute()
        )
        emb = rows_of(svc.table("media_embeddings").select("is_outlier").in_("deployment_id", deployment_ids).execute())
        return obs, emb

    observations, embeddings = await asyncio.to_thread(_fetch)

    species_counts: dict[str, int] = {}
    funnel: dict[str, int] = {}
    for o in observations:
        name = o.get("scientific_name")
        if name and o.get("observation_type") == "animal":
            species_counts[name] = species_counts.get(name, 0) + 1
        status = o.get("review_status") or "unreviewed"
        funnel[status] = funnel.get(status, 0) + 1

    species = sorted(
        ({"scientific_name": n, "count": c, "under_represented": c < 10} for n, c in species_counts.items()),
        key=lambda s: -s["count"],
    )
    n_outliers = sum(1 for e in embeddings if e.get("is_outlier"))
    outlier_rate = round(n_outliers / len(embeddings), 4) if embeddings else None

    return {
        "project_id": project_id,
        "deployments": len(deployment_ids),
        "species": species,
        "species_count": len(species),
        "review_funnel": funnel,
        "outlier_rate": outlier_rate,
        "total_observations": len(observations),
    }


async def list_alerts(project_id: str) -> list[dict]:
    from app.services.supabase_client import create_service_client

    svc = create_service_client()

    def _fetch():
        resp = (
            svc.table("conservation_alerts")
            .select("*")
            .eq("project_id", project_id)
            .is_("acknowledged_at", "null")
            .order("first_seen", desc=True)
            .execute()
        )
        return rows_of(resp)

    return await asyncio.to_thread(_fetch)


async def unknown_species(org_id: str) -> list[dict]:
    """Provisional 'candidate' taxa awaiting expert confirmation."""
    from app.services.supabase_client import create_service_client

    svc = create_service_client()

    def _fetch():
        resp = svc.table("taxa").select("id, scientific_name, common_name, status").eq("status", "candidate").execute()
        return rows_of(resp)

    return await asyncio.to_thread(_fetch)


async def occupancy(project_id: str) -> dict:
    """Pairwise species-assemblage overlap (Jaccard) between a project's deployments."""
    from app.services.supabase_client import create_service_client

    svc = create_service_client()
    deployment_ids = await _project_deployment_ids(project_id)

    def _fetch():
        resp = (
            svc.table("observations")
            .select("deployment_id, scientific_name")
            .in_("deployment_id", deployment_ids)
            .eq("source_type", "human")
            .is_("deleted_at", "null")
            .execute()
        )
        return rows_of(resp)

    rows = await asyncio.to_thread(_fetch) if deployment_ids else []
    taxa_by_dep: dict[str, set] = {}
    for r in rows:
        if r.get("scientific_name"):
            taxa_by_dep.setdefault(r["deployment_id"], set()).add(r["scientific_name"])

    pairs = []
    deps = sorted(taxa_by_dep)
    for i in range(len(deps)):
        for j in range(i + 1, len(deps)):
            pairs.append({"a": deps[i], "b": deps[j], "jaccard": jaccard(taxa_by_dep[deps[i]], taxa_by_dep[deps[j]])})
    return {"project_id": project_id, "deployments": len(deps), "pairs": sorted(pairs, key=lambda p: -p["jaccard"])}


async def accumulation(deployment_id: str) -> dict:
    """Species accumulation curve over time for a deployment."""
    from app.services.supabase_client import create_service_client

    svc = create_service_client()

    def _fetch():
        resp = (
            svc.table("observations")
            .select("scientific_name, classification_timestamp, created_at")
            .eq("deployment_id", deployment_id)
            .eq("observation_type", "animal")
            .is_("deleted_at", "null")
            .execute()
        )
        return rows_of(resp)

    rows = await asyncio.to_thread(_fetch)
    rows.sort(key=lambda o: o.get("classification_timestamp") or o.get("created_at") or "")
    curve = accumulation_curve([o.get("scientific_name") for o in rows])
    return {"deployment_id": deployment_id, "n_observations": len(curve), "total_species": curve[-1] if curve else 0, "curve": curve}
