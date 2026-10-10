# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Wildlife Brain router — DINOv3 embeddings, clusters, similarity.

Thin HTTP layer (envelope responses); all logic in domain/wildlife_brain.py and
the pgvector vector store. Gated behind ``FF_WILDLIFE_BRAIN_ENABLED``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, Query, Request

from app.authz import (
    accessible_deployment_ids,
    assert_access,
    require_cluster_access,
    require_deployment_access,
    require_media_access,
    require_project_access,
    require_system_admin,
)
from app.config import settings
from app.dependencies import get_current_user, get_verified_user, require_not_demo
from app.middleware.rate_limit import limiter
from app.schemas.brain import ConfirmClusterRequest, EmbedRequest, MultiClusterRequest, ReprocessAllRequest, ReprocessRequest, ReviewDecisionRequest
from app.schemas.common import ApiError, ApiMeta, ApiResponse
from app.services.db_utils import row_of, rows_of
from app.services.supabase_client import create_service_client

logger = structlog.get_logger()

router = APIRouter(prefix="/api/brain", tags=["brain"])


def _disabled(req_id):
    return ApiResponse(
        error=ApiError(code="FEATURE_DISABLED", message="Wildlife Brain is disabled (FF_WILDLIFE_BRAIN_ENABLED)."),
        meta=ApiMeta(request_id=req_id),
    )


def _al_disabled(req_id):
    return ApiResponse(
        error=ApiError(code="FEATURE_DISABLED", message="Active learning is disabled (FF_ACTIVE_LEARNING_ENABLED)."),
        meta=ApiMeta(request_id=req_id),
    )


@router.post("/embed/{deployment_id}", dependencies=[Depends(require_deployment_access)])
@limiter.limit("10/minute")
async def embed_deployment(request: Request, deployment_id: str, body: EmbedRequest | None = None, user=Depends(get_verified_user)):
    """Embed + cluster a deployment. Server mode enqueues a GPU job."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_WILDLIFE_BRAIN_ENABLED:
        return _disabled(req_id)
    body = body or EmbedRequest()

    if body.mode == "client_vectors":
        if not settings.FF_LOCAL_EMBEDDING_ENABLED:
            return ApiResponse(
                error=ApiError(code="FEATURE_DISABLED", message="Local embedding disabled (FF_LOCAL_EMBEDDING_ENABLED)."),
                meta=ApiMeta(request_id=req_id),
            )
        return ApiResponse(
            error=ApiError(code="NOT_IMPLEMENTED", message="client_vectors ingestion lands in the WebGPU slice."),
            meta=ApiMeta(request_id=req_id),
        )

    from app.jobs.dispatch import enqueue_job
    from app.jobs.store import create_job

    job_id = await create_job(user_id=user.id, kind="ai_embed", label=f"Embed deployment {deployment_id[:8]}", deployment_ids=[deployment_id])
    await enqueue_job("embed_deployment_job", job_id, deployment_id, body.model_name)
    return ApiResponse(
        data={"job_id": job_id, "status": "queued", "deployment_id": deployment_id},
        meta=ApiMeta(request_id=req_id),
    )


@router.get("/clusters/{deployment_id}", dependencies=[Depends(require_deployment_access)])
async def list_clusters(request: Request, deployment_id: str, user=Depends(get_current_user)):
    """List HDBSCAN clusters for the deployment's latest embedding run."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_WILDLIFE_BRAIN_ENABLED:
        return _disabled(req_id)

    svc = create_service_client()

    def _fetch():
        latest = (
            svc.table("embedding_runs")
            .select("id")
            .eq("deployment_id", deployment_id)
            .eq("status", "complete")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if not latest.data:
            return None, []
        run_id = rows_of(latest)[0]["id"]
        clusters = (
            svc.table("cluster_assignments")
            .select("*")
            .eq("deployment_id", deployment_id)
            .eq("embedding_run_id", run_id)
            .order("image_count", desc=True)
            .execute()
        )
        return run_id, rows_of(clusters)

    run_id, clusters = await asyncio.to_thread(_fetch)
    return ApiResponse(data={"embedding_run_id": run_id, "clusters": clusters}, meta=ApiMeta(request_id=req_id))


@router.post("/clusters/multi", dependencies=[Depends(require_not_demo)])
async def multi_clusters(request: Request, body: MultiClusterRequest, user=Depends(get_current_user)):
    """Aggregate clusters across multiple deployments.

    Returns clusters from each deployment's latest completed embedding run,
    grouped by model variant (ViT-H vs ViT-S are separate vector spaces).
    Optionally filters member media by cluster_confidence >= min_confidence.
    """
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_WILDLIFE_BRAIN_ENABLED:
        return _disabled(req_id)

    if not body.deployment_ids:
        return ApiResponse(data={"clusters": [], "media_clusters": {}, "outlier_media_ids": [], "model_groups": []}, meta=ApiMeta(request_id=req_id))

    # Object-level authz: restrict to deployments the caller may access (body list,
    # so it can't use the path-param dependency). An empty result hides the rest.
    body.deployment_ids = await accessible_deployment_ids(user.id, body.deployment_ids)
    if not body.deployment_ids:
        return ApiResponse(data={"clusters": [], "media_clusters": {}, "outlier_media_ids": [], "model_groups": []}, meta=ApiMeta(request_id=req_id))

    svc = create_service_client()

    def _fetch():
        # 1. Get the latest completed embedding run per deployment.
        runs_resp = (
            svc.table("embedding_runs")
            .select("id, deployment_id, model_name")
            .in_("deployment_id", body.deployment_ids)
            .eq("status", "complete")
            .order("created_at", desc=True)
            .execute()
        )
        if not runs_resp.data:
            return [], {}, [], []

        # Keep only the latest run per deployment.
        seen: dict[str, dict] = {}
        for r in rows_of(runs_resp):
            if r["deployment_id"] not in seen:
                seen[r["deployment_id"]] = r
        latest_runs = list(seen.values())
        run_ids = [r["id"] for r in latest_runs]

        # 2. Fetch cluster assignments for all runs.
        clusters_resp = svc.table("cluster_assignments").select("*").in_("embedding_run_id", run_ids).order("image_count", desc=True).execute()
        clusters = rows_of(clusters_resp)

        # 3. Fetch member media per cluster (with optional confidence filter).
        members_query = (
            svc.table("media_embeddings")
            .select("media_id, cluster_id, deployment_id, cluster_confidence, is_outlier")
            .in_("deployment_id", body.deployment_ids)
        )
        if body.min_confidence > 0:
            members_query = members_query.gte("cluster_confidence", body.min_confidence)
        members_resp = members_query.execute()
        members = rows_of(members_resp)

        # 4. Group by model variant (cross-variant is not meaningful).
        run_model = {r["id"]: r.get("model_name", "unknown") for r in latest_runs}
        model_groups: dict[str, list[str]] = {}
        for r in latest_runs:
            model = r.get("model_name", "unknown")
            model_groups.setdefault(model, []).append(r["deployment_id"])

        # Enrich clusters with their model variant.
        for c in clusters:
            c["model_name"] = run_model.get(c.get("embedding_run_id", ""), "unknown")

        outlier_ids = [m["media_id"] for m in members if m.get("is_outlier")]
        # media_id → cluster_id for non-outlier members, so the grid can group
        # each image under its cluster (not just separate outliers).
        media_clusters = {m["media_id"]: m["cluster_id"] for m in members if not m.get("is_outlier")}

        return clusters, media_clusters, outlier_ids, [{"model_name": k, "deployment_ids": v} for k, v in model_groups.items()]

    clusters, media_clusters, outlier_ids, model_groups = await asyncio.to_thread(_fetch)
    return ApiResponse(
        data={"clusters": clusters, "media_clusters": media_clusters, "outlier_media_ids": outlier_ids, "model_groups": model_groups},
        meta=ApiMeta(request_id=req_id),
    )


@router.get("/umap/{deployment_id}", dependencies=[Depends(require_deployment_access)])
async def umap_coords(request: Request, deployment_id: str, user=Depends(get_current_user)):
    """UMAP scatter data for the deployment (stable persisted coords)."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_WILDLIFE_BRAIN_ENABLED:
        return _disabled(req_id)

    svc = create_service_client()

    def _fetch():
        resp = (
            svc.table("media_embeddings")
            .select("media_id, umap_x, umap_y, cluster_id, is_outlier, cluster_purity")
            .eq("deployment_id", deployment_id)
            .execute()
        )
        return rows_of(resp)

    points = await asyncio.to_thread(_fetch)
    return ApiResponse(data={"points": points, "count": len(points)}, meta=ApiMeta(request_id=req_id))


@router.get("/outliers/{deployment_id}", dependencies=[Depends(require_deployment_access)])
async def list_outliers(request: Request, deployment_id: str, user=Depends(get_current_user)):
    """HDBSCAN-rejected images (candidate expert-review queue)."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_WILDLIFE_BRAIN_ENABLED:
        return _disabled(req_id)

    svc = create_service_client()

    def _fetch():
        resp = (
            svc.table("media_embeddings")
            .select("media_id, cluster_confidence")
            .eq("deployment_id", deployment_id)
            .eq("is_outlier", True)
            .order("cluster_confidence", desc=False)
            .execute()
        )
        return rows_of(resp)

    rows = await asyncio.to_thread(_fetch)
    return ApiResponse(data={"outliers": rows, "count": len(rows)}, meta=ApiMeta(request_id=req_id))


@router.get("/similar/{media_id}", dependencies=[Depends(require_media_access)])
async def similar(request: Request, media_id: str, n: int = Query(20, ge=1, le=100), org_scoped: bool = True, user=Depends(get_current_user)):
    """pgvector nearest-neighbour search for a media item."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_WILDLIFE_BRAIN_ENABLED:
        return _disabled(req_id)

    from app.services.vector_store import get_vector_service

    svc = create_service_client()

    def _run_model():
        me = svc.table("media_embeddings").select("embedding_run_id").eq("media_id", media_id).maybe_single().execute()
        run_id = (row_of(me) or {}).get("embedding_run_id")
        if not run_id:
            return None
        run = svc.table("embedding_runs").select("model_name").eq("id", run_id).maybe_single().execute()
        return (row_of(run) or {}).get("model_name")

    model_name = await asyncio.to_thread(_run_model)
    store = get_vector_service(model_name)  # per-model vector space (falls back to default)
    vector = await store.retrieve_vector(media_id)
    if vector is None:
        return ApiResponse(error=ApiError(code="NOT_FOUND", message="No embedding for this media"), meta=ApiMeta(request_id=req_id))

    # Exclude self at the DB level so exactly n neighbours come back.
    results = await store.search(vector, limit=n, exclude_media_id=media_id)
    hits = [{"media_id": r.id, "score": r.score, "payload": r.payload} for r in results]
    return ApiResponse(data={"media_id": media_id, "results": hits}, meta=ApiMeta(request_id=req_id))


@router.post("/clusters/{cluster_assignment_id}/confirm", dependencies=[Depends(require_cluster_access), Depends(require_not_demo)])
async def confirm_cluster(request: Request, cluster_assignment_id: str, body: ConfirmClusterRequest, user=Depends(get_current_user)):
    """Confirm a cluster as a taxon — bulk-creates human observations for members."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_WILDLIFE_BRAIN_ENABLED:
        return _disabled(req_id)

    svc = create_service_client()
    now = datetime.now(timezone.utc).isoformat()

    def _confirm():
        assignment = row_of(svc.table("cluster_assignments").select("*").eq("id", cluster_assignment_id).maybe_single().execute())
        if not assignment:
            return None, 0
        members = (
            svc.table("media_embeddings")
            .select("media_id")
            .eq("deployment_id", assignment["deployment_id"])
            .eq("embedding_run_id", assignment["embedding_run_id"])
            .eq("cluster_id", assignment["cluster_id"])
            .execute()
        )
        obs_rows = [
            {
                "deployment_id": assignment["deployment_id"],
                "media_id": m["media_id"],
                "observation_level": "media",
                "observation_type": "animal",
                "taxon_id": body.taxon_id,
                "scientific_name": body.scientific_name,
                "vernacular_name": body.vernacular_name,
                "source_type": "human",
                "review_status": "human_reviewed",
                "classification_method": "human",
                "reviewer_id": user.id,
                "embedding_run_id": assignment["embedding_run_id"],
                "cluster_id": assignment["cluster_id"],
            }
            for m in rows_of(members)
        ]
        for i in range(0, len(obs_rows), 50):
            svc.table("observations").insert(obs_rows[i : i + 50]).execute()

        svc.table("cluster_assignments").update(
            {
                "taxon_id": body.taxon_id,
                "scientific_name": body.scientific_name,
                "review_state": "confirmed",
                "confirmed_by": user.id,
                "confirmed_at": now,
            }
        ).eq("id", cluster_assignment_id).execute()
        return assignment, len(obs_rows)

    assignment, created = await asyncio.to_thread(_confirm)
    if assignment is None:
        return ApiResponse(error=ApiError(code="NOT_FOUND", message="Cluster assignment not found"), meta=ApiMeta(request_id=req_id))
    return ApiResponse(
        data={"cluster_assignment_id": cluster_assignment_id, "observations_created": created},
        meta=ApiMeta(request_id=req_id),
    )


# ── Embedding lifecycle (Phase 5.5) ──────────────────────────────────


@router.get("/embedding-runs/{deployment_id}", dependencies=[Depends(require_deployment_access)])
async def embedding_runs(request: Request, deployment_id: str, user=Depends(get_current_user)):
    """List embedding runs for a deployment (model version, status, image count)."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_WILDLIFE_BRAIN_ENABLED:
        return _disabled(req_id)

    from app.domain.embedding_lifecycle import list_embedding_runs

    runs = await list_embedding_runs(deployment_id)
    return ApiResponse(data={"runs": runs}, meta=ApiMeta(request_id=req_id))


@router.post("/reprocess/deployment/{deployment_id}", dependencies=[Depends(require_deployment_access)])
async def reprocess_deployment_endpoint(request: Request, deployment_id: str, body: ReprocessRequest | None = None, user=Depends(get_verified_user)):
    """Supersede current runs and re-embed a deployment (new embedding_run)."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_WILDLIFE_BRAIN_ENABLED:
        return _disabled(req_id)
    body = body or ReprocessRequest()

    from app.jobs.dispatch import enqueue_job
    from app.jobs.store import create_job

    job_id = await create_job(user_id=user.id, kind="ai_reprocess", label=f"Reprocess deployment {deployment_id[:8]}", deployment_ids=[deployment_id])
    await enqueue_job("reprocess_deployment_job", job_id, deployment_id, body.model_name)
    return ApiResponse(data={"job_id": job_id, "status": "queued", "deployment_id": deployment_id}, meta=ApiMeta(request_id=req_id))


@router.post("/reprocess/project/{project_id}", dependencies=[Depends(require_project_access)])
async def reprocess_project_endpoint(request: Request, project_id: str, body: ReprocessRequest | None = None, user=Depends(get_verified_user)):
    """Reprocess all deployments in a project."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_WILDLIFE_BRAIN_ENABLED:
        return _disabled(req_id)
    body = body or ReprocessRequest()

    from app.jobs.dispatch import enqueue_job
    from app.jobs.store import create_job

    job_id = await create_job(user_id=user.id, kind="ai_reprocess", label=f"Reprocess project {project_id[:8]}")
    await enqueue_job("reprocess_project_job", job_id, project_id, body.model_name)
    return ApiResponse(data={"job_id": job_id, "status": "queued", "project_id": project_id}, meta=ApiMeta(request_id=req_id))


@router.post("/reprocess/all", dependencies=[Depends(require_system_admin)])
async def reprocess_all_endpoint(request: Request, body: ReprocessAllRequest | None = None, user=Depends(get_verified_user)):
    """Platform-wide re-embed. Default dry-run returns a cost estimate; executing requires confirm=true."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_WILDLIFE_BRAIN_ENABLED:
        return _disabled(req_id)
    body = body or ReprocessAllRequest()

    if body.dry_run or not body.confirm:
        from app.domain.embedding_lifecycle import reprocess_all

        estimate = await reprocess_all(model_name=body.model_name, dry_run=True)
        if not body.dry_run and not body.confirm:
            estimate["note"] = "Set confirm=true (and dry_run=false) to execute this global re-embed."
        return ApiResponse(data=estimate, meta=ApiMeta(request_id=req_id))

    from app.jobs.dispatch import enqueue_job
    from app.jobs.store import create_job

    job_id = await create_job(user_id=user.id, kind="ai_reprocess", label="Reprocess all deployments")
    await enqueue_job("reprocess_all_job", job_id, body.model_name)
    return ApiResponse(data={"job_id": job_id, "status": "queued", "scope": "global"}, meta=ApiMeta(request_id=req_id))


@router.get("/compare-runs")
async def compare_runs_endpoint(request: Request, run_a: str = Query(...), run_b: str = Query(...), user=Depends(get_current_user)):
    """Compare cluster assignments between two embedding runs."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_WILDLIFE_BRAIN_ENABLED:
        return _disabled(req_id)

    # Object-level authz: both runs (query params) resolve to a deployment the
    # caller must be able to access.
    svc = create_service_client()

    def _run_deployment(run_id: str):
        resp = svc.table("embedding_runs").select("deployment_id").eq("id", run_id).limit(1).execute()
        rows = rows_of(resp)
        return rows[0]["deployment_id"] if rows else None

    for _rid in (run_a, run_b):
        await assert_access(user.id, deployment_id=await asyncio.to_thread(_run_deployment, _rid))

    from app.domain.embedding_lifecycle import compare_runs

    result = await compare_runs(run_a, run_b)
    return ApiResponse(data=result, meta=ApiMeta(request_id=req_id))


# ── Active learning + review queue (Phase 8) ─────────────────────────


@router.post("/recalculate-al-scores/{deployment_id}", dependencies=[Depends(require_deployment_access), Depends(require_not_demo)])
async def recalculate_al_scores(request: Request, deployment_id: str, user=Depends(get_current_user)):
    """Enqueue an active-learning score recompute for a deployment."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_ACTIVE_LEARNING_ENABLED:
        return _al_disabled(req_id)

    from app.jobs.dispatch import enqueue_job
    from app.jobs.store import create_job

    job_id = await create_job(
        user_id=user.id, kind="active_learning", label=f"Recompute AL scores {deployment_id[:8]}", deployment_ids=[deployment_id]
    )
    await enqueue_job("recompute_al_job", job_id, deployment_id)
    return ApiResponse(data={"job_id": job_id, "status": "queued", "deployment_id": deployment_id}, meta=ApiMeta(request_id=req_id))


@router.get("/review-queue/{deployment_id}", dependencies=[Depends(require_deployment_access)])
async def review_queue(request: Request, deployment_id: str, limit: int = Query(50, ge=1, le=200), user=Depends(get_current_user)):
    """Media ranked by active_learning_score DESC, with AI label + score reasons."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_ACTIVE_LEARNING_ENABLED:
        return _al_disabled(req_id)

    from app.domain.active_learning import get_review_queue

    rows = await get_review_queue(deployment_id, limit)
    return ApiResponse(data={"queue": rows, "count": len(rows)}, meta=ApiMeta(request_id=req_id))


@router.post("/review/{media_id}", dependencies=[Depends(require_media_access), Depends(require_not_demo)])
async def review_media(request: Request, media_id: str, body: ReviewDecisionRequest, user=Depends(get_current_user)):
    """Record a reviewer decision on one media item (creates a human observation)."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_WILDLIFE_BRAIN_ENABLED:
        return _disabled(req_id)

    svc = create_service_client()
    review_status = "expert_reviewed" if body.decision == "expert" else "human_reviewed"

    def _write():
        media = row_of(svc.table("media").select("deployment_id").eq("id", media_id).maybe_single().execute())
        if not media:
            return False
        svc.table("observations").insert(
            {
                "deployment_id": media["deployment_id"],
                "media_id": media_id,
                "observation_level": "media",
                "observation_type": "animal",
                "taxon_id": body.taxon_id,
                "scientific_name": body.scientific_name,
                "vernacular_name": body.vernacular_name,
                "source_type": "human",
                "review_status": review_status,
                "classification_method": "human",
                "reviewer_id": user.id,
            }
        ).execute()
        return True

    ok = await asyncio.to_thread(_write)
    if not ok:
        return ApiResponse(error=ApiError(code="NOT_FOUND", message="Media not found"), meta=ApiMeta(request_id=req_id))
    return ApiResponse(data={"media_id": media_id, "decision": body.decision, "review_status": review_status}, meta=ApiMeta(request_id=req_id))


@router.post("/backup", dependencies=[Depends(require_system_admin)])
async def backup_embeddings_endpoint(request: Request, user=Depends(get_current_user)):
    """Vector-store DR. Retained for compatibility; a no-op now that embeddings live
    in Postgres (pgvector) under Supabase PITR — the job completes with nothing to do.
    """
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_WILDLIFE_BRAIN_ENABLED:
        return _disabled(req_id)

    from app.jobs.dispatch import enqueue_job
    from app.jobs.store import create_job

    job_id = await create_job(user_id=user.id, kind="maintenance", label="Embeddings backup")
    await enqueue_job("embeddings_backup_job", job_id)
    return ApiResponse(data={"job_id": job_id, "status": "queued"}, meta=ApiMeta(request_id=req_id))
