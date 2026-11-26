# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the embedding registry — the single source of truth for DINOv3.

These guard the invariants the whole Wildlife Brain depends on: each variant has
its own dim + collection (ViT-H 1280-d, ViT-S 384-d), model names match what
ww-backend persists, and the WebGPU client config stays in lock-step with the
server preprocessing for its variant.
"""

import pytest

from app.registries.embedding_registry import (
    DEFAULT_LOCAL_MODEL,
    DEFAULT_SERVER_MODEL,
    HDBSCAN_PRESETS,
    EmbeddingModelName,
    ExecutionProvider,
    get_embedding_dim,
    get_local_embedding_config,
    get_model_spec,
    get_vector_space,
    is_valid_model_name,
    purity_bucket,
    review_depth_for_purity,
)


def test_model_names_match_backend_enum():
    # Must equal the CHECK constraint on embedding_runs.model_name in ww-backend.
    assert {m.value for m in EmbeddingModelName} == {"dinov3-vith", "dinov3-vits"}


def test_variants_have_distinct_dims_and_collections():
    vith = get_model_spec(EmbeddingModelName.DINOV3_VITH)
    vits = get_model_spec(EmbeddingModelName.DINOV3_VITS)
    assert vith.embedding_dim == 1280
    assert vits.embedding_dim == 384
    # Different dims → must be different collections.
    assert vith.vector_space != vits.vector_space
    assert get_embedding_dim("dinov3-vith") == 1280
    assert get_embedding_dim("dinov3-vits") == 384
    assert get_vector_space("dinov3-vits") == "media_embeddings_vits"


def test_local_variant_uses_onnx_community_model():
    spec = get_model_spec(EmbeddingModelName.DINOV3_VITS)
    assert spec.onnx_artifact_id == "onnx-community/dinov3-vits16-pretrain-lvd1689m-ONNX"
    # ViT-H is server-only (too large for browser WebGPU).
    assert get_model_spec(EmbeddingModelName.DINOV3_VITH).onnx_artifact_id is None


def test_defaults_target_expected_providers():
    assert DEFAULT_SERVER_MODEL is EmbeddingModelName.DINOV3_VITH
    assert DEFAULT_LOCAL_MODEL is EmbeddingModelName.DINOV3_VITS
    assert get_model_spec(DEFAULT_LOCAL_MODEL).default_provider is ExecutionProvider.WEBGPU


def test_get_model_spec_accepts_str_and_enum():
    assert get_model_spec("dinov3-vits").name is EmbeddingModelName.DINOV3_VITS
    assert get_model_spec(EmbeddingModelName.DINOV3_VITH).hf_model_id.startswith("facebook/")


def test_get_model_spec_rejects_unknown():
    with pytest.raises(ValueError, match="Unknown embedding model"):
        get_model_spec("dinov2-giant")  # 768d fallback is explicitly not allowed


def test_is_valid_model_name():
    assert is_valid_model_name("dinov3-vith")
    assert not is_valid_model_name("dinov3-vitb")


@pytest.mark.parametrize(
    ("purity", "depth", "bucket"),
    [
        (0.95, "bulk", "high"),
        (0.85, "bulk", "high"),
        (0.70, "sample", "medium"),
        (0.65, "sample", "medium"),
        (0.40, "full", "low"),
    ],
)
def test_purity_mapping(purity, depth, bucket):
    assert review_depth_for_purity(purity) == depth
    assert purity_bucket(purity) == bucket


def test_local_config_matches_local_variant_space():
    cfg = get_local_embedding_config()
    spec = get_model_spec(DEFAULT_LOCAL_MODEL)
    # Client must use the local variant's own dim + collection (384-d ViT-S).
    assert cfg["model_name"] == "dinov3-vits"
    assert cfg["embedding_dim"] == 384 == spec.embedding_dim
    assert cfg["vector_space"] == spec.vector_space == "media_embeddings_vits"
    assert cfg["execution_provider"] == "webgpu"
    assert cfg["preprocessing"]["pooling"] == spec.preprocessing.pooling
    assert cfg["preprocessing"]["image_mean"] == list(spec.preprocessing.image_mean)
    assert cfg["preprocessing"]["size"] == spec.preprocessing.size


def test_hdbscan_small_preset():
    small = HDBSCAN_PRESETS["small"]
    assert (small.min_cluster_size, small.min_samples) == (15, 5)
