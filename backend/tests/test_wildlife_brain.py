# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for Wildlife Brain pure helpers (numpy only — no torch/hdbscan/umap)."""

import pytest

from app.domain.wildlife_brain import (
    build_cluster_assignment_rows,
    build_media_embedding_rows,
    cluster_hdbscan,
    compute_cluster_purities,
    reduce_umap,
    summarize_clusters,
)
from app.services.dinov3 import chunk


def test_summarize_clusters_counts_and_outlier_ordering():
    labels = [0, 0, 1, -1]
    confs = [0.9, 0.7, 0.5, 0.1]
    summaries = summarize_clusters(labels, confs)
    by_id = {s.cluster_id: s for s in summaries}
    assert by_id[0].image_count == 2
    assert by_id[0].mean_confidence == pytest.approx(0.8)
    assert by_id[1].image_count == 1
    assert by_id[-1].is_outlier is True
    # Outliers sort last.
    assert summaries[-1].cluster_id == -1


def test_purity_tight_clusters_are_high():
    emb = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0]]
    labels = [0, 0, 1, 1]
    purities = compute_cluster_purities(emb, labels)
    assert purities[0] == pytest.approx(1.0)
    assert purities[1] == pytest.approx(1.0)


def test_purity_scattered_cluster_is_low_and_outliers_excluded():
    emb = [[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0], [0.0, -1.0], [9.0, 9.0]]
    labels = [0, 0, 0, 0, -1]  # opposing members cancel → centroid ~0 → purity 0
    purities = compute_cluster_purities(emb, labels)
    assert purities[0] == pytest.approx(0.0, abs=1e-6)
    assert -1 not in purities  # outliers excluded


def test_build_media_embedding_rows_shape():
    rows = build_media_embedding_rows(
        media_ids=["m1", "m2"],
        deployment_id="dep",
        embedding_run_id="run",
        cluster_labels=[0, -1],
        cluster_probs=[0.9, 0.0],
        purities={0: 0.92},
        umap_xy=[(1.0, 2.0), (3.0, 4.0)],
    )
    assert rows[0]["cluster_purity"] == "high"  # 0.92 → high bucket
    assert rows[0]["is_outlier"] is False
    assert rows[0]["media_id"] == "m1"
    assert rows[1]["is_outlier"] is True
    assert rows[1]["cluster_purity"] is None  # outlier label has no purity
    assert (rows[0]["umap_x"], rows[0]["umap_y"]) == (1.0, 2.0)


def test_build_cluster_assignment_rows_review_depth():
    summaries = summarize_clusters([0, 0, 1, -1], [0.9, 0.9, 0.5, 0.1])
    rows = build_cluster_assignment_rows("dep", "run", summaries, {0: 0.9, 1: 0.5})
    by_cluster = {r["cluster_id"]: r for r in rows}
    assert by_cluster[0]["review_depth"] == "bulk"  # 0.9 ≥ 0.85
    assert by_cluster[1]["review_depth"] == "full"  # 0.5 < 0.65
    assert by_cluster[-1]["review_depth"] is None  # outlier cluster
    assert by_cluster[-1]["is_outlier_cluster"] is True


def test_cluster_hdbscan_fallback_for_tiny_input():
    # Below MIN_CLUSTERABLE (4) there is nothing to cluster → single group, and the
    # hdbscan import is skipped (keeps this test hermetic without the package).
    emb = [[0.0, 1.0]] * 3
    labels, probs = cluster_hdbscan(emb)
    assert labels == [0] * 3
    assert probs == [1.0] * 3


def test_reduce_umap_tiny_input_returns_zeros():
    coords = reduce_umap([[1.0, 2.0]] * 3, n_components=2, params={"n_neighbors": 15})
    assert coords == [(0.0, 0.0)] * 3


def test_chunk_batches():
    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    assert chunk([], 3) == []
    with pytest.raises(ValueError):
        chunk([1], 0)


async def test_resolve_crops_concurrent_preserves_order_and_filters(monkeypatch):
    # Order alignment is correctness-critical: images[k] must stay paired with
    # kept_ids[k] (and the embeddings computed from them). Failures are dropped.
    import app.domain.media_resolver as mr
    from app.domain.wildlife_brain import _resolve_crops_concurrent

    async def fake_resolve(url, size="full"):
        if url == "bad":
            return None  # unresolvable crop → dropped
        return (f"bytes:{url}".encode(), "image/jpeg")

    monkeypatch.setattr(mr, "resolve_media", fake_resolve)

    crops = [
        {"id": "m1", "deployment_id": "d1", "crop_url": "u1"},
        {"id": "m2", "deployment_id": "d1", "crop_url": "bad"},
        {"id": "m3", "deployment_id": "d2", "crop_url": "u3"},
    ]
    out = await _resolve_crops_concurrent(crops, concurrency=2)

    assert [mid for (mid, _d, _b) in out] == ["m1", "m3"]  # m2 dropped, order kept
    assert [dep for (_m, dep, _b) in out] == ["d1", "d2"]
    assert out[0][2] == b"bytes:u1"


@pytest.mark.parametrize("model_name", [None, ""])
async def test_embed_without_a_model_name_uses_the_default_service(monkeypatch, model_name):
    # A blank model_name in the request used to skip the default service and then
    # read .model_name off None, so the job died before creating its run.
    import app.domain.wildlife_brain as wb
    import app.services.dinov3 as dv

    class FakeDino:
        model_name = "dinov3-default"
        version = "hf/dinov3"

    created: dict = {}

    async def fake_create(deployment_id, model, version, created_by):
        created.update(model=model, version=version)
        return "run-1"

    async def no_crops(deployment_id):
        return []

    async def finish(*_args):
        return None

    monkeypatch.setattr(wb, "ensure_embedding_available", lambda *_a: None)
    monkeypatch.setattr(dv, "get_dinov3_service", FakeDino)
    monkeypatch.setattr(wb, "_create_embedding_run", fake_create)
    monkeypatch.setattr(wb, "_fetch_crops", no_crops)
    monkeypatch.setattr(wb, "_finish_embedding_run", finish)

    out = await wb.embed_and_cluster_deployment("dep-1", model_name=model_name)

    assert created == {"model": "dinov3-default", "version": "hf/dinov3"}
    assert out["image_count"] == 0
