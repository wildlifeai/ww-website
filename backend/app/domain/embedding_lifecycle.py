# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Embedding lifecycle — versioning, reprocessing, and run comparison.

Lets embedding models evolve safely after deployment: every re-embed creates a
new ``embedding_runs`` row, marks the prior run ``superseded`` (its
``cluster_assignments`` are retained for history), and overwrites the current
``media_embeddings``. Provides dry-run cost estimates and run comparison. Vector
DR needs nothing here — vectors live in Postgres (pgvector) under Supabase PITR.

Layering: orchestration here; DINOv3/Supabase in services (lazy). The cost and
comparison helpers are pure and unit-tested.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import structlog
from postgrest import CountMethod

from app.services.db_utils import rows_of

logger = structlog.get_logger()

# DINOv3 ViT-H/16+ ≈ 50–100 ms/image on A100; A100 ≈ $3/hr (one-off embed cost).
DEFAULT_MS_PER_IMAGE = 75.0
DEFAULT_GPU_USD_PER_HOUR = 3.0


# ── Pure helpers ─────────────────────────────────────────────────────


def estimate_embedding_cost(
    image_count: int,
    ms_per_image: float = DEFAULT_MS_PER_IMAGE,
    gpu_usd_per_hour: float = DEFAULT_GPU_USD_PER_HOUR,
) -> dict:
    """Estimate one-off GPU time + cost to (re)embed ``image_count`` images."""
    gpu_hours = image_count * ms_per_image / 1000.0 / 3600.0
    return {
        "image_count": image_count,
        "ms_per_image": ms_per_image,
        "gpu_hours": round(gpu_hours, 2),
        "est_usd": round(gpu_hours * gpu_usd_per_hour, 2),
    }


def summarize_run_comparison(rows_a: list[dict], rows_b: list[dict]) -> dict:
    """Compare two embedding runs' ``cluster_assignments`` (pure).

    Per-media label-change rate needs per-(media, run) history (media_embeddings is
    1:1/current-only); this compares at the cluster + confirmed-taxon level, which
    is what's retained across runs.
    """

    def confirmed_taxa(rows):
        return {r["taxon_id"] for r in rows if r.get("taxon_id")}

    def n_clusters(rows):
        return len([r for r in rows if not r.get("is_outlier_cluster")])

    def total_images(rows):
        return sum(int(r.get("image_count") or 0) for r in rows)

    ta, tb = confirmed_taxa(rows_a), confirmed_taxa(rows_b)
    return {
        "run_a": {"clusters": n_clusters(rows_a), "confirmed_taxa": len(ta), "images": total_images(rows_a)},
        "run_b": {"clusters": n_clusters(rows_b), "confirmed_taxa": len(tb), "images": total_images(rows_b)},
        "cluster_delta": n_clusters(rows_b) - n_clusters(rows_a),
        "taxa_added": sorted(tb - ta),
        "taxa_removed": sorted(ta - tb),
    }


# ── Run registry ─────────────────────────────────────────────────────


async def list_embedding_runs(deployment_id: str) -> list[dict]:
    from app.services.supabase_client import create_service_client

    svc = create_service_client()

    def _fetch():
        resp = (
            svc.table("embedding_runs")
            .select("id, model_name, model_version, status, image_count, created_at, completed_at")
            .eq("deployment_id", deployment_id)
            .order("created_at", desc=True)
            .execute()
        )
        return rows_of(resp)

    return await asyncio.to_thread(_fetch)


async def _supersede_complete_runs(deployment_id: str) -> None:
    from app.services.supabase_client import create_service_client

    svc = create_service_client()

    def _do():
        svc.table("embedding_runs").update({"status": "superseded"}).eq("deployment_id", deployment_id).eq("status", "complete").execute()

    await asyncio.to_thread(_do)


# ── Reprocessing ─────────────────────────────────────────────────────


async def reprocess_deployment(deployment_id: str, model_name: Optional[str] = None, created_by: Optional[str] = None, progress=None) -> dict:
    """Mark current runs superseded, then re-embed + recluster the deployment."""
    from app.domain.wildlife_brain import embed_and_cluster_deployment, ensure_embedding_available

    # Check first: superseding hides the current clusters until the new run completes.
    ensure_embedding_available(model_name)
    await _supersede_complete_runs(deployment_id)
    logger.info("reprocess_deployment", deployment_id=deployment_id, model=model_name)
    return await embed_and_cluster_deployment(deployment_id, model_name=model_name, created_by=created_by, progress=progress)


async def reprocess_project(project_id: str, model_name: Optional[str] = None, created_by: Optional[str] = None, progress=None) -> dict:
    """Reprocess every deployment in a project (sequentially)."""
    from app.services.supabase_client import create_service_client

    svc = create_service_client()

    def _deps():
        resp = svc.table("deployments").select("id").eq("project_id", project_id).is_("deleted_at", "null").execute()
        return [d["id"] for d in rows_of(resp)]

    deployment_ids = await asyncio.to_thread(_deps)
    results = []
    for i, dep in enumerate(deployment_ids):
        if progress:
            await progress(i / max(1, len(deployment_ids)), f"Deployment {i + 1}/{len(deployment_ids)}")
        results.append(await reprocess_deployment(dep, model_name=model_name, created_by=created_by))
    return {"project_id": project_id, "deployments": len(deployment_ids), "runs": results}


async def _count_active_media() -> int:
    from app.services.supabase_client import create_service_client

    svc = create_service_client()

    def _count():
        resp = svc.table("media").select("id", count=CountMethod.exact).is_("deleted_at", "null").execute()
        return resp.count or 0

    return await asyncio.to_thread(_count)


async def reprocess_all(model_name: Optional[str] = None, dry_run: bool = True, created_by: Optional[str] = None, progress=None) -> dict:
    """Platform-wide re-embed. ``dry_run`` returns a cost estimate without executing."""
    total = await _count_active_media()
    if dry_run:
        return {"dry_run": True, "model_name": model_name, **estimate_embedding_cost(total)}

    from app.services.supabase_client import create_service_client

    svc = create_service_client()

    def _deps():
        resp = svc.table("deployments").select("id").is_("deleted_at", "null").execute()
        return [d["id"] for d in rows_of(resp)]

    deployment_ids = await asyncio.to_thread(_deps)
    for i, dep in enumerate(deployment_ids):
        if progress:
            await progress(i / max(1, len(deployment_ids)), f"Deployment {i + 1}/{len(deployment_ids)}")
        await reprocess_deployment(dep, model_name=model_name, created_by=created_by)
    return {"dry_run": False, "deployments": len(deployment_ids), **estimate_embedding_cost(total)}


# ── Comparison ───────────────────────────────────────────────────────


async def compare_runs(run_a: str, run_b: str) -> dict:
    from app.services.supabase_client import create_service_client

    svc = create_service_client()

    def _fetch(run_id):
        resp = (
            svc.table("cluster_assignments").select("cluster_id, taxon_id, image_count, is_outlier_cluster").eq("embedding_run_id", run_id).execute()
        )
        return rows_of(resp)

    rows_a = await asyncio.to_thread(_fetch, run_a)
    rows_b = await asyncio.to_thread(_fetch, run_b)
    return {"run_a_id": run_a, "run_b_id": run_b, **summarize_run_comparison(rows_a, rows_b)}


# ── Disaster recovery ────────────────────────────────────────────────


async def backup_embeddings_snapshot() -> Optional[str]:
    """No-op — vectors now live in Postgres (``media_embeddings.embedding``) and are
    covered by Supabase PITR, so there is no separate vector-store snapshot to take.

    Kept so the ``/api/brain/backup`` endpoint stays valid; returns None (nothing to do).
    """
    logger.info("embeddings_backup_noop", reason="pgvector covered by Supabase PITR")
    return None
