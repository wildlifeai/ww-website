# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Wildlife Brain — DINOv3 zero-shot clustering (pure domain logic).

Pipeline: animal crops → DINOv3 1280-d embeddings → UMAP (2D persisted, 50D for
clustering) → HDBSCAN clusters → purity scoring → vector upsert (pgvector) + Supabase write
(media_embeddings, cluster_assignments, embedding_runs).

Distinct from ``domain/clustering.py`` (perceptual-hash near-duplicate detection
for iNaturalist) — this is semantic, embedding-based species grouping.

Layering: orchestration here; DINOv3 / clustering libs live in services
and are imported lazily. The summary + purity helpers are pure (numpy only) and
unit-tested without torch/hdbscan/umap.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

import numpy as np
import structlog

from app.registries.embedding_registry import (
    DEFAULT_HDBSCAN_PRESET,
    HDBSCAN_PRESETS,
    UMAP_CLUSTER_PARAMS,
    UMAP_PERSIST_PARAMS,
    get_collection,
    get_embedding_dim,
    purity_bucket,
    review_depth_for_purity,
)
from app.services.db_utils import rows_of

logger = structlog.get_logger()


async def _resolve_crops_concurrent(crops: list[dict], *, concurrency: int = 10) -> list[tuple[str, str, bytes]]:
    """Resolve crop URLs to bytes with bounded concurrency, preserving input order.

    Returns ``(media_id, deployment_id, image_bytes)`` per successfully-resolved
    crop. Replaces a sequential ``await resolve_media`` loop that was very slow for
    large deployments/projects (each resolve is network I/O to Drive / a public URL).
    """
    from app.domain.media_resolver import resolve_media

    sem = asyncio.Semaphore(concurrency)

    async def _one(c: dict):
        async with sem:
            try:
                resolved = await resolve_media(c["crop_url"], size="full")
            except Exception as exc:  # noqa: BLE001 — one bad crop must not sink the run
                logger.warning("crop_resolve_failed", media_id=c.get("id"), error=str(exc))
                return None
        return (c["id"], c["deployment_id"], resolved[0]) if resolved else None

    results = await asyncio.gather(*[_one(c) for c in crops])
    return [r for r in results if r is not None]


OUTLIER_LABEL = -1


# ── Pure helpers (numpy only — unit-testable) ────────────────────────


@dataclass(frozen=True)
class ClusterSummary:
    cluster_id: int
    image_count: int
    mean_confidence: float
    is_outlier: bool


def summarize_clusters(
    cluster_labels: list[int],
    confidences: Optional[list[float]] = None,
) -> list[ClusterSummary]:
    """Aggregate per-cluster counts and mean confidence (label -1 = outliers)."""
    confidences = confidences or [0.0] * len(cluster_labels)
    buckets: dict[int, list[float]] = {}
    for label, conf in zip(cluster_labels, confidences):
        buckets.setdefault(int(label), []).append(float(conf))
    summaries = [
        ClusterSummary(
            cluster_id=label,
            image_count=len(confs),
            mean_confidence=round(sum(confs) / len(confs), 4) if confs else 0.0,
            is_outlier=(label == OUTLIER_LABEL),
        )
        for label, confs in buckets.items()
    ]
    # Largest non-outlier clusters first; outliers last.
    return sorted(summaries, key=lambda s: (s.is_outlier, -s.image_count))


def _l2_normalize(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


def compute_cluster_purities(embeddings: list[list[float]], cluster_labels: list[int]) -> dict[int, float]:
    """Purity per cluster = mean cosine similarity of members to their centroid.

    Tight clusters (members visually alike) → high purity (→ 'bulk' review);
    loose/mixed clusters → low purity (→ 'full' review). Outliers (-1) excluded.
    Returns ``{cluster_id: purity in [0, 1]}``.

    (Inter-cluster proximity penalty is a documented follow-up; intra-cluster
    cohesion is the primary, well-behaved signal.)
    """
    if not embeddings:
        return {}
    mat = _l2_normalize(np.asarray(embeddings, dtype=np.float32))
    labels = np.asarray(cluster_labels)
    purities: dict[int, float] = {}
    for label in sorted(set(int(x) for x in labels)):
        if label == OUTLIER_LABEL:
            continue
        members = mat[labels == label]
        if len(members) == 0:
            continue
        centroid = members.mean(axis=0)
        norm = np.linalg.norm(centroid)
        if norm == 0:
            purities[label] = 0.0
            continue
        centroid = centroid / norm
        intra = float(np.clip(np.mean(members @ centroid), 0.0, 1.0))
        purities[label] = round(intra, 4)
    return purities


def build_media_embedding_rows(
    media_ids: list[str],
    deployment_id: str,
    embedding_run_id: str,
    cluster_labels: list[int],
    cluster_probs: list[float],
    purities: dict[int, float],
    umap_xy: Sequence[Sequence[float]],
) -> list[dict]:
    """Build media_embeddings upsert rows (pure)."""
    rows: list[dict] = []
    for i, media_id in enumerate(media_ids):
        label = int(cluster_labels[i])
        purity = purities.get(label)
        rows.append(
            {
                "media_id": media_id,
                "deployment_id": deployment_id,
                "embedding_run_id": embedding_run_id,
                "cluster_id": label,
                "cluster_confidence": round(float(cluster_probs[i]), 4),
                "cluster_purity": purity_bucket(purity) if purity is not None else None,
                "is_outlier": label == OUTLIER_LABEL,
                "umap_x": float(umap_xy[i][0]),
                "umap_y": float(umap_xy[i][1]),
            }
        )
    return rows


def build_cluster_assignment_rows(
    deployment_id: str,
    embedding_run_id: str,
    summaries: list[ClusterSummary],
    purities: dict[int, float],
) -> list[dict]:
    """Build cluster_assignments rows (pure) — one per cluster, unconfirmed."""
    rows: list[dict] = []
    for s in summaries:
        purity = purities.get(s.cluster_id)
        rows.append(
            {
                "id": str(uuid.uuid4()),
                "deployment_id": deployment_id,
                "embedding_run_id": embedding_run_id,
                "cluster_id": s.cluster_id,
                "is_outlier_cluster": s.is_outlier,
                "image_count": s.image_count,
                "mean_confidence": s.mean_confidence,
                "purity_score": purity,
                "review_depth": review_depth_for_purity(purity) if (purity is not None and not s.is_outlier) else None,
                "review_state": "open",
            }
        )
    return rows


def build_media_embedding_rows_scoped(
    media_ids: list[str],
    deployment_ids: list[str],
    embedding_run_id: str,
    cluster_labels: list[int],
    cluster_probs: list[float],
    purities: dict[int, float],
    umap_xy: Sequence[Sequence[float]],
) -> list[dict]:
    """Like ``build_media_embedding_rows`` but each media carries its own deployment_id.

    Used by multi-deployment (project/global) runs where one global cluster_id can
    span deployments.
    """
    rows: list[dict] = []
    for i, media_id in enumerate(media_ids):
        label = int(cluster_labels[i])
        purity = purities.get(label)
        rows.append(
            {
                "media_id": media_id,
                "deployment_id": deployment_ids[i],
                "embedding_run_id": embedding_run_id,
                "cluster_id": label,
                "cluster_confidence": round(float(cluster_probs[i]), 4),
                "cluster_purity": purity_bucket(purity) if purity is not None else None,
                "is_outlier": label == OUTLIER_LABEL,
                "umap_x": float(umap_xy[i][0]),
                "umap_y": float(umap_xy[i][1]),
            }
        )
    return rows


def build_scoped_cluster_assignment_rows(
    embedding_run_id: str,
    media_deployment_ids: list[str],
    cluster_labels: list[int],
    cluster_probs: list[float],
    purities: dict[int, float],
) -> list[dict]:
    """Per-(deployment, cluster) cluster_assignments slices for a multi-deployment run.

    A single global HDBSCAN ``cluster_id`` can span deployments, but the schema keys
    cluster_assignments by ``(deployment_id, embedding_run_id, cluster_id)`` (composite
    unique). So we emit one row per (deployment, cluster) intersection: ``image_count``
    and ``mean_confidence`` are per-slice, while ``purity_score`` is the global cluster
    value (purity is computed across all members of the cluster regardless of deployment).
    """
    slices: dict[tuple[str, int], list[float]] = {}
    for dep_id, label, prob in zip(media_deployment_ids, cluster_labels, cluster_probs):
        slices.setdefault((dep_id, int(label)), []).append(float(prob))

    rows: list[dict] = []
    for (dep_id, label), probs in slices.items():
        purity = purities.get(label)
        is_outlier = label == OUTLIER_LABEL
        rows.append(
            {
                "id": str(uuid.uuid4()),
                "deployment_id": dep_id,
                "embedding_run_id": embedding_run_id,
                "cluster_id": label,
                "is_outlier_cluster": is_outlier,
                "image_count": len(probs),
                "mean_confidence": round(sum(probs) / len(probs), 4) if probs else 0.0,
                "purity_score": purity,
                "review_depth": review_depth_for_purity(purity) if (purity is not None and not is_outlier) else None,
                "review_state": "open",
            }
        )
    return rows


# ── Lazy ML wrappers ─────────────────────────────────────────────────


# Below this many crops a UMAP projection is meaningless (it returns all-zero
# coordinates), so we cluster on the raw embeddings instead — see
# ``prepare_cluster_input``. Below ``MIN_CLUSTERABLE`` we don't cluster at all.
MIN_UMAP_POINTS = 10
MIN_CLUSTERABLE = 4


def prepare_cluster_input(embeddings: list[list[float]]) -> list[tuple[float, ...]]:
    """Build the feature matrix HDBSCAN clusters on.

    For enough points, reduce to 50-D with UMAP (denoises + speeds up HDBSCAN).
    For small sets UMAP can't project (it would emit all-zero coords and destroy
    the signal), so fall back to the L2-normalized raw embeddings — euclidean
    distance on those is equivalent to cosine, which is what we want.
    """
    n = len(embeddings)
    if n == 0:
        # _l2_normalize would index axis=1 of a 1-D empty array and raise.
        return []
    # Reduce to 50-D only with comfortably more points than target dims. UMAP's
    # spectral init computes ~n_components eigenvectors and raises "k >= N" once
    # n_components (50) + 1 >= n — so a small/medium set (e.g. a 10-crop CamtrapDP
    # import) must cluster on the raw (L2-normalized) embeddings instead.
    if n > 60:
        return reduce_umap(embeddings, 50, UMAP_CLUSTER_PARAMS)
    normalized = _l2_normalize(np.asarray(embeddings, dtype=np.float32))
    return [tuple(float(v) for v in row) for row in normalized]


def cluster_hdbscan(embeddings: Sequence[Sequence[float]], preset: str = DEFAULT_HDBSCAN_PRESET) -> tuple[list[int], list[float]]:
    """Cluster embeddings with HDBSCAN. Returns (labels, probabilities).

    The preset's ``min_cluster_size`` (15 for 'small') is tuned for full
    deployments. For smaller sets it is scaled down to ``n // 2`` so they still
    form real clusters instead of collapsing into a single fallback group —
    which is what made "Group by Cluster" look broken on small/test deployments.
    Below ``MIN_CLUSTERABLE`` points there is nothing meaningful to cluster, so
    everything lands in one group.
    """
    cfg = HDBSCAN_PRESETS.get(preset, HDBSCAN_PRESETS[DEFAULT_HDBSCAN_PRESET])
    n = len(embeddings)
    if n < MIN_CLUSTERABLE:
        return [0] * n, [1.0] * n

    # Scale the preset to the dataset so small sets still cluster.
    min_cluster_size = max(2, min(cfg.min_cluster_size, n // 2))
    min_samples = max(1, min(cfg.min_samples, min_cluster_size))

    import hdbscan  # lazy

    clusterer = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size, min_samples=min_samples, metric="euclidean")
    labels = clusterer.fit_predict(np.asarray(embeddings, dtype=np.float32))
    probs = getattr(clusterer, "probabilities_", np.ones(n))
    return [int(x) for x in labels], [float(x) for x in probs]


def reduce_umap(embeddings: list[list[float]], n_components: int, params: dict) -> list[tuple[float, ...]]:
    """UMAP-reduce embeddings to ``n_components``. No-op-ish for tiny inputs."""
    n = len(embeddings)
    # UMAP's spectral init raises "k >= N" (scipy eigh) unless n comfortably exceeds both
    # n_components and n_neighbors. Floor accordingly (n_components + 2 guards the target
    # dim; MIN_UMAP_POINTS keeps tiny projections meaningless-but-safe).
    if n < max(MIN_UMAP_POINTS, n_components + 2, params.get("n_neighbors", 15) // 3):
        # Too few points for a meaningful projection.
        return [tuple([0.0] * n_components) for _ in range(n)]

    import umap  # lazy (umap-learn)

    reducer = umap.UMAP(
        n_components=n_components,
        n_neighbors=min(params.get("n_neighbors", 15), n - 1),
        min_dist=params.get("min_dist", 0.1),
        metric=params.get("metric", "cosine"),
        random_state=params.get("random_state", 42),
    )
    coords = reducer.fit_transform(np.asarray(embeddings, dtype=np.float32))
    return [tuple(float(v) for v in row) for row in coords]


# ── Orchestration ────────────────────────────────────────────────────


async def _create_embedding_run(deployment_id: str, model_name: str, model_version: str, created_by: Optional[str]) -> str:
    from app.services.supabase_client import create_service_client

    svc = create_service_client()
    run_id = str(uuid.uuid4())

    def _insert():
        svc.table("embedding_runs").insert(
            {
                "id": run_id,
                "scope": "deployment",
                "scope_id": deployment_id,
                "deployment_id": deployment_id,
                "model_name": model_name,
                "model_version": model_version,
                "embedding_dim": get_embedding_dim(model_name),
                "execution_provider": "server_gpu",
                "reduction_method": "umap",
                "reduction_params": UMAP_PERSIST_PARAMS,
                "clustering_method": "hdbscan",
                "clustering_params": {"preset": DEFAULT_HDBSCAN_PRESET},
                "qdrant_collection": get_collection(model_name),
                "status": "running",
                "created_by": created_by,
            }
        ).execute()

    await asyncio.to_thread(_insert)
    return run_id


async def _finish_embedding_run(run_id: str, status: str, image_count: int) -> None:
    from app.services.supabase_client import create_service_client

    svc = create_service_client()

    def _update():
        svc.table("embedding_runs").update({"status": status, "image_count": image_count, "completed_at": datetime.now(timezone.utc).isoformat()}).eq(
            "id", run_id
        ).execute()

    await asyncio.to_thread(_update)


async def _fetch_crops(deployment_id: str) -> list[dict]:
    """Return media rows for the deployment that have an animal_crop_url."""
    from app.services.supabase_client import create_service_client

    svc = create_service_client()

    def _fetch():
        resp = (
            svc.table("media")
            .select("id, deployment_id, media_assets!inner(animal_crop_url)")
            .eq("deployment_id", deployment_id)
            .is_("deleted_at", "null")
            .execute()
        )
        rows = []
        for r in rows_of(resp):
            assets = r.get("media_assets")
            if isinstance(assets, list):
                assets = assets[0] if assets else None
            crop = (assets or {}).get("animal_crop_url")
            if crop:
                rows.append({"id": r["id"], "deployment_id": r["deployment_id"], "crop_url": crop})
        return rows

    return await asyncio.to_thread(_fetch)


async def embed_and_cluster_deployment(
    deployment_id: str,
    model_name: Optional[str] = None,
    created_by: Optional[str] = None,
    progress=None,
) -> dict:
    """Embed a deployment's animal crops, cluster them, and persist everything.

    Returns a summary dict. ``progress`` is an optional async callable(float, str).
    """
    from app.services.dinov3 import get_dinov3_service
    from app.services.supabase_client import create_service_client
    from app.services.vector_store import build_payload, get_vector_service

    async def _tick(pct: float, msg: str):
        if progress:
            await progress(pct, msg)

    if model_name:
        dino, resolved_model, model_version = None, model_name, model_name
    else:  # None or a blank name from the request: the configured default
        dino = get_dinov3_service()
        resolved_model, model_version = dino.model_name, dino.version

    run_id = await _create_embedding_run(deployment_id, resolved_model, model_version, created_by)
    logger.info("wildlife_brain_run_start", deployment_id=deployment_id, run_id=run_id, model=resolved_model)
    await _tick(0.05, "Fetching animal crops…")

    try:
        crops = await _fetch_crops(deployment_id)
        if not crops:
            await _finish_embedding_run(run_id, "complete", 0)
            return {"embedding_run_id": run_id, "image_count": 0, "clusters": 0, "message": "no animal crops"}

        media_ids = [c["id"] for c in crops]

        await _tick(0.15, f"Downloading {len(crops)} crops…")
        resolved_crops = await _resolve_crops_concurrent(crops)
        images: list[bytes] = [img for (_mid, _dep, img) in resolved_crops]
        kept_ids: list[str] = [mid for (mid, _dep, _img) in resolved_crops]
        media_ids = kept_ids
        if not images:
            await _finish_embedding_run(run_id, "complete", 0)
            return {"embedding_run_id": run_id, "image_count": 0, "clusters": 0, "message": "crops unresolvable"}

        await _tick(0.35, f"Embedding {len(images)} crops with DINOv3…")
        dino = dino or get_dinov3_service()
        embeddings = await dino.embed(images)

        await _tick(0.65, "Reducing + clustering…")
        umap_2d = await asyncio.to_thread(reduce_umap, embeddings, 2, UMAP_PERSIST_PARAMS)
        cluster_input = await asyncio.to_thread(prepare_cluster_input, embeddings)
        labels, probs = await asyncio.to_thread(cluster_hdbscan, cluster_input)
        purities = compute_cluster_purities(embeddings, labels)
        summaries = summarize_clusters(labels, probs)

        await _tick(0.8, "Storing vectors…")
        store = get_vector_service(resolved_model)
        payloads = [build_payload(deployment_id=deployment_id, embedding_run_id=run_id, cluster_id=int(labels[i])) for i in range(len(media_ids))]
        await store.upsert(media_ids, embeddings, payloads)

        await _tick(0.9, "Writing results…")
        me_rows = build_media_embedding_rows(media_ids, deployment_id, run_id, labels, probs, purities, umap_2d)
        ca_rows = build_cluster_assignment_rows(deployment_id, run_id, summaries, purities)

        svc = create_service_client()

        def _persist():
            for i in range(0, len(me_rows), 100):
                svc.table("media_embeddings").upsert(me_rows[i : i + 100], on_conflict="media_id").execute()
            if ca_rows:
                svc.table("cluster_assignments").upsert(ca_rows, on_conflict="deployment_id,embedding_run_id,cluster_id").execute()

        await asyncio.to_thread(_persist)

        await _finish_embedding_run(run_id, "complete", len(media_ids))
        n_clusters = len([s for s in summaries if not s.is_outlier])
        await _tick(1.0, "Done")
        logger.info("wildlife_brain_run_complete", run_id=run_id, images=len(media_ids), clusters=n_clusters)
        return {"embedding_run_id": run_id, "image_count": len(media_ids), "clusters": n_clusters}
    except Exception as exc:
        logger.error("wildlife_brain_run_failed", run_id=run_id, error=str(exc))
        await _finish_embedding_run(run_id, "failed", 0)
        raise


# ── Scoped clustering (project / global / unreviewed) ────────────────


async def _resolve_scope_deployments(scope: str, scope_id: Optional[str], owner_user_id: Optional[str]) -> list[str]:
    """Resolve the deployment ids a scoped run should cover.

    - ``project``: all deployments in the project (``scope_id``).
    - ``global``: every deployment the requesting user can see (RLS-respecting via
      the user's accessible projects); ``owner_user_id`` required.
    """
    from app.services.supabase_client import create_service_client

    svc = create_service_client()

    def _fetch() -> list[str]:
        q = svc.table("deployments").select("id").is_("deleted_at", "null")
        if scope == "project":
            q = q.eq("project_id", scope_id)
        resp = q.execute()
        return [r["id"] for r in rows_of(resp)]

    return await asyncio.to_thread(_fetch)


async def _fetch_crops_for_deployments(deployment_ids: list[str], only_unreviewed: bool) -> list[dict]:
    """Animal crops across many deployments. Returns rows of {id, deployment_id, crop_url}.

    ``only_unreviewed`` excludes media that already carry a human/expert observation,
    so cross-project runs can focus on the unreviewed backlog.
    """
    from app.services.supabase_client import create_service_client

    svc = create_service_client()

    def _fetch() -> list[dict]:
        rows: list[dict] = []
        reviewed: set[str] = set()
        if only_unreviewed:
            rev = (
                svc.table("observations")
                .select("media_id")
                .in_("deployment_id", deployment_ids)
                .in_("review_status", ["human_reviewed", "expert_reviewed", "consensus_approved"])
                .not_.is_("media_id", "null")
                .execute()
            )
            reviewed = {r["media_id"] for r in rows_of(rev) if r.get("media_id")}

        # Chunk the IN() to keep URLs/queries bounded for large scopes.
        for i in range(0, len(deployment_ids), 50):
            chunk = deployment_ids[i : i + 50]
            resp = (
                svc.table("media")
                .select("id, deployment_id, media_assets!inner(animal_crop_url)")
                .in_("deployment_id", chunk)
                .is_("deleted_at", "null")
                .execute()
            )
            for r in rows_of(resp):
                if only_unreviewed and r["id"] in reviewed:
                    continue
                assets = r.get("media_assets")
                if isinstance(assets, list):
                    assets = assets[0] if assets else None
                crop = (assets or {}).get("animal_crop_url")
                if crop:
                    rows.append({"id": r["id"], "deployment_id": r["deployment_id"], "crop_url": crop})
        return rows

    return await asyncio.to_thread(_fetch)


async def _create_scoped_embedding_run(
    scope: str,
    scope_id: Optional[str],
    project_id: Optional[str],
    model_name: str,
    model_version: str,
    created_by: Optional[str],
) -> str:
    from app.services.supabase_client import create_service_client

    svc = create_service_client()
    run_id = str(uuid.uuid4())

    def _insert():
        svc.table("embedding_runs").insert(
            {
                "id": run_id,
                "scope": scope,
                "scope_id": scope_id,  # NULL for global; project_id for project (chk_*_scope_match)
                "project_id": project_id,
                "model_name": model_name,
                "model_version": model_version,
                "embedding_dim": get_embedding_dim(model_name),
                "execution_provider": "server_gpu",
                "reduction_method": "umap",
                "reduction_params": UMAP_PERSIST_PARAMS,
                "clustering_method": "hdbscan",
                "clustering_params": {"preset": DEFAULT_HDBSCAN_PRESET},
                "qdrant_collection": get_collection(model_name),
                "status": "running",
                "created_by": created_by,
            }
        ).execute()

    await asyncio.to_thread(_insert)
    return run_id


async def embed_and_cluster_scope(
    scope: str,
    scope_id: Optional[str] = None,
    project_id: Optional[str] = None,
    model_name: Optional[str] = None,
    only_unreviewed: bool = False,
    created_by: Optional[str] = None,
    progress=None,
) -> dict:
    """Embed + cluster animal crops across many deployments (project or global scope).

    Produces ONE embedding_run with a global cluster numbering; ``media_embeddings``
    rows are overwritten per media (1:1 "current" state), and ``cluster_assignments``
    are written as per-(deployment, cluster) slices sharing the global cluster_id — so
    "cluster 5" means the same visual group across every deployment in the scope.

    ``scope='project'`` requires ``scope_id == project_id``. ``scope='global'`` uses
    ``scope_id=None`` and considers every deployment the caller can see.
    """
    from app.services.dinov3 import get_dinov3_service
    from app.services.supabase_client import create_service_client
    from app.services.vector_store import build_payload, get_vector_service

    async def _tick(pct: float, msg: str):
        if progress:
            await progress(pct, msg)

    if scope not in ("project", "global"):
        raise ValueError("embed_and_cluster_scope only handles 'project' or 'global' (use embed_and_cluster_deployment for a single deployment)")
    if scope == "project" and not scope_id:
        raise ValueError("project scope requires scope_id (the project id)")

    if model_name:
        dino, resolved_model, model_version = None, model_name, model_name
    else:  # None or a blank name from the request: the configured default
        dino = get_dinov3_service()
        resolved_model, model_version = dino.model_name, dino.version

    # project scope: scope_id must equal project_id (chk_project_scope_match).
    run_project_id = scope_id if scope == "project" else None
    run_id = await _create_scoped_embedding_run(scope, scope_id, run_project_id, resolved_model, model_version, created_by)
    logger.info("wildlife_brain_scope_start", scope=scope, scope_id=scope_id, run_id=run_id, model=resolved_model)

    try:
        await _tick(0.05, "Resolving deployments…")
        deployment_ids = await _resolve_scope_deployments(scope, scope_id, created_by)
        if not deployment_ids:
            await _finish_embedding_run(run_id, "complete", 0)
            return {"embedding_run_id": run_id, "image_count": 0, "clusters": 0, "deployments": 0, "message": "no deployments in scope"}

        await _tick(0.1, f"Fetching crops across {len(deployment_ids)} deployments…")
        crops = await _fetch_crops_for_deployments(deployment_ids, only_unreviewed)
        if not crops:
            await _finish_embedding_run(run_id, "complete", 0)
            return {
                "embedding_run_id": run_id,
                "image_count": 0,
                "clusters": 0,
                "deployments": len(deployment_ids),
                "message": "no animal crops in scope",
            }

        await _tick(0.2, f"Downloading {len(crops)} crops…")
        resolved_crops = await _resolve_crops_concurrent(crops)
        images: list[bytes] = [img for (_mid, _dep, img) in resolved_crops]
        kept_ids: list[str] = [mid for (mid, _dep, _img) in resolved_crops]
        kept_deps: list[str] = [dep for (_mid, dep, _img) in resolved_crops]
        if not images:
            await _finish_embedding_run(run_id, "complete", 0)
            return {"embedding_run_id": run_id, "image_count": 0, "clusters": 0, "deployments": len(deployment_ids), "message": "crops unresolvable"}

        await _tick(0.4, f"Embedding {len(images)} crops with DINOv3…")
        dino = dino or get_dinov3_service()
        embeddings = await dino.embed(images)

        await _tick(0.65, "Reducing + clustering across scope…")
        umap_2d = await asyncio.to_thread(reduce_umap, embeddings, 2, UMAP_PERSIST_PARAMS)
        cluster_input = await asyncio.to_thread(prepare_cluster_input, embeddings)
        labels, probs = await asyncio.to_thread(cluster_hdbscan, cluster_input)
        purities = compute_cluster_purities(embeddings, labels)

        await _tick(0.8, "Storing vectors…")
        store = get_vector_service(resolved_model)
        payloads = [build_payload(deployment_id=kept_deps[i], embedding_run_id=run_id, cluster_id=int(labels[i])) for i in range(len(kept_ids))]
        await store.upsert(kept_ids, embeddings, payloads)

        await _tick(0.9, "Writing results…")
        me_rows = build_media_embedding_rows_scoped(kept_ids, kept_deps, run_id, labels, probs, purities, umap_2d)
        ca_rows = build_scoped_cluster_assignment_rows(run_id, kept_deps, labels, probs, purities)

        svc = create_service_client()

        def _persist():
            for i in range(0, len(me_rows), 100):
                svc.table("media_embeddings").upsert(me_rows[i : i + 100], on_conflict="media_id").execute()
            for i in range(0, len(ca_rows), 100):
                svc.table("cluster_assignments").upsert(ca_rows[i : i + 100], on_conflict="deployment_id,embedding_run_id,cluster_id").execute()

        await asyncio.to_thread(_persist)

        await _finish_embedding_run(run_id, "complete", len(kept_ids))
        n_clusters = len({int(label) for label in labels if int(label) != OUTLIER_LABEL})
        await _tick(1.0, "Done")
        logger.info("wildlife_brain_scope_complete", run_id=run_id, images=len(kept_ids), clusters=n_clusters, deployments=len(deployment_ids))
        return {
            "embedding_run_id": run_id,
            "image_count": len(kept_ids),
            "clusters": n_clusters,
            "deployments": len(deployment_ids),
        }
    except Exception as exc:
        logger.error("wildlife_brain_scope_failed", run_id=run_id, error=str(exc))
        await _finish_embedding_run(run_id, "failed", 0)
        raise
