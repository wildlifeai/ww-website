# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Embedding model registry — single source of truth for the Wildlife Brain.

Defines the DINOv3 variants, their **per-variant** vector space
(``embedding_runs.vector_space``), the exact image preprocessing/pooling constants, and the HDBSCAN /
UMAP / active-learning parameters used across the v4 pipeline.

Per-variant dims (important): the server variant (ViT-H/16+) emits 1280-d; the
local WebGPU variant (ViT-S/16) emits 384-d. They are **different vector spaces**,
so each has its own vector space. Similarity is valid *within* a variant;
cross-variant (local ↔ server) similarity is not meaningful without a learned
projection. ``embedding_runs.embedding_dim`` + ``embedding_runs.vector_space``
record which space each run used.

Why this module is deliberately dependency-free (stdlib only):

- The API process imports it to validate requests and serve config WITHOUT pulling
  in torch / transformers (those live in the GPU worker image only).
- ``get_local_embedding_config()`` returns the JSON the in-browser WebGPU path
  consumes, so the frontend mirrors the SAME model id, dim, collection, and
  preprocessing the server uses for that variant.

Reuse this registry instead of hardcoding model names or preprocessing anywhere
else (per the backend agent skill: "do not invent model names").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# ── Model variants ───────────────────────────────────────────────────


class EmbeddingModelName(str, Enum):
    """DINOv3 variants. The value is the canonical ``model_name`` persisted on
    ``embedding_runs.model_name`` (ww-backend) and sent by the WebGPU client.

    Each variant has its own dim + vector space (see module docstring).
    """

    DINOV3_VITH = "dinov3-vith"  # ViT-H/16+ — server (Azure GPU). 1280-d. Highest quality.
    DINOV3_VITS = "dinov3-vits"  # ViT-S/16 — local WebGPU / CPU dev. 384-d. Smaller, faster.


class ExecutionProvider(str, Enum):
    """Where embeddings were computed (mirrors ``embedding_runs.execution_provider``)."""

    SERVER_GPU = "server_gpu"
    SERVER_CPU = "server_cpu"
    WEBGPU = "webgpu"


# Default (server / ViT-H) geometry. Per-variant values live on the specs below.
EMBEDDING_DIM = 1280


@dataclass(frozen=True)
class ImagePreprocessing:
    """Preprocessing applied to an animal crop before the model.

    These values MUST be identical on the server (services/dinov3.py) and the
    in-browser WebGPU extractor for the SAME variant, or its embeddings won't be
    comparable. Values follow the DINOv3 image processor defaults.
    """

    size: int = 224  # square resize (longest-edge → size, center crop to size×size)
    resample: str = "bicubic"
    rescale_factor: float = 1.0 / 255.0
    image_mean: tuple[float, float, float] = (0.485, 0.456, 0.406)
    image_std: tuple[float, float, float] = (0.229, 0.224, 0.225)
    pooling: str = "cls"  # use the CLS token as the embedding


@dataclass(frozen=True)
class EmbeddingModelSpec:
    """Everything needed to load a variant and route its vectors."""

    name: EmbeddingModelName
    hf_model_id: str  # HuggingFace repo id (server load via transformers)
    onnx_artifact_id: str | None  # ONNX repo for the WebGPU client (None = server-only)
    embedding_dim: int  # output dim for THIS variant (1280 ViT-H, 384 ViT-S)
    vector_space: str  # per-variant space (different dims cannot share one), embedding_runs.vector_space
    gated: bool = False  # HF repo requires access approval
    default_provider: ExecutionProvider = ExecutionProvider.SERVER_CPU
    preprocessing: ImagePreprocessing = field(default_factory=ImagePreprocessing)


_REGISTRY: dict[EmbeddingModelName, EmbeddingModelSpec] = {
    EmbeddingModelName.DINOV3_VITH: EmbeddingModelSpec(
        name=EmbeddingModelName.DINOV3_VITH,
        hf_model_id="facebook/dinov3-vith16plus-pretrain-lvd1689m",
        onnx_artifact_id=None,  # too large for browser WebGPU; server-only
        embedding_dim=1280,
        vector_space="media_embeddings",
        gated=True,
        default_provider=ExecutionProvider.SERVER_GPU,
    ),
    EmbeddingModelName.DINOV3_VITS: EmbeddingModelSpec(
        name=EmbeddingModelName.DINOV3_VITS,
        hf_model_id="facebook/dinov3-vits16-pretrain-lvd1689m",
        onnx_artifact_id="onnx-community/dinov3-vits16-pretrain-lvd1689m-ONNX",  # Transformers.js + WebGPU
        embedding_dim=384,
        vector_space="media_embeddings_vits",
        gated=True,  # facebook repo is gated; the onnx-community mirror is open (WebGPU path)
        default_provider=ExecutionProvider.WEBGPU,
    ),
}

# Default server variant when the caller does not specify one.
DEFAULT_SERVER_MODEL = EmbeddingModelName.DINOV3_VITH
# Default local (in-browser) variant.
DEFAULT_LOCAL_MODEL = EmbeddingModelName.DINOV3_VITS


def get_model_spec(model_name: str | EmbeddingModelName) -> EmbeddingModelSpec:
    """Return the spec for a variant.

    Raises:
        ValueError: if ``model_name`` is not a registered variant.
    """
    try:
        key = EmbeddingModelName(model_name)
    except ValueError as exc:
        valid = ", ".join(m.value for m in EmbeddingModelName)
        raise ValueError(f"Unknown embedding model '{model_name}'. Valid: {valid}") from exc
    return _REGISTRY[key]


def is_valid_model_name(model_name: str) -> bool:
    """True if ``model_name`` is a registered variant."""
    return model_name in (m.value for m in EmbeddingModelName)


def get_embedding_dim(model_name: str | EmbeddingModelName) -> int:
    """Output dimensionality for a variant (1280 ViT-H, 384 ViT-S)."""
    return get_model_spec(model_name).embedding_dim


def get_vector_space(model_name: str | EmbeddingModelName) -> str:
    """The logical vector space of a variant (one per variant: dimensions differ)."""
    return get_model_spec(model_name).vector_space


# ── Clustering / reduction presets ───────────────────────────────────


@dataclass(frozen=True)
class HdbscanPreset:
    min_cluster_size: int
    min_samples: int


HDBSCAN_PRESETS: dict[str, HdbscanPreset] = {
    "small": HdbscanPreset(min_cluster_size=15, min_samples=5),
    "medium": HdbscanPreset(min_cluster_size=30, min_samples=10),
    "large": HdbscanPreset(min_cluster_size=50, min_samples=15),
}
DEFAULT_HDBSCAN_PRESET = "small"

# UMAP: 2D coords are persisted (stable); a 50-D reduction feeds clustering.
UMAP_PERSIST_PARAMS = {"n_components": 2, "n_neighbors": 15, "min_dist": 0.1, "metric": "cosine", "random_state": 42}
UMAP_CLUSTER_PARAMS = {"n_components": 50, "n_neighbors": 15, "min_dist": 0.0, "metric": "cosine", "random_state": 42}


# ── Cluster purity → review depth ────────────────────────────────────

PURITY_HIGH_THRESHOLD = 0.85
PURITY_SAMPLE_THRESHOLD = 0.65


def review_depth_for_purity(purity: float) -> str:
    """Map a cluster purity score to a review depth.

    >= 0.85 → 'bulk'  (reviewer confirms whole cluster from a sample grid)
    0.65-0.84 → 'sample' (confirm or escalate from a 20% sample)
    < 0.65 → 'full'   (likely mixed cluster — inspect every image)
    """
    if purity >= PURITY_HIGH_THRESHOLD:
        return "bulk"
    if purity >= PURITY_SAMPLE_THRESHOLD:
        return "sample"
    return "full"


def purity_bucket(purity: float) -> str:
    """Coarse purity label persisted on ``media_embeddings.cluster_purity``."""
    if purity >= PURITY_HIGH_THRESHOLD:
        return "high"
    if purity >= PURITY_SAMPLE_THRESHOLD:
        return "medium"
    return "low"


# ── Active learning score weights (Phase 8) ──────────────────────────

ACTIVE_LEARNING_WEIGHTS = {
    "novelty": 0.35,
    "uncertainty": 0.35,
    "disagreement": 0.20,
    "outlier_boost": 0.10,
}


# ── Frontend / WebGPU export ─────────────────────────────────────────


def get_local_embedding_config() -> dict:
    """Config the in-browser WebGPU extractor needs to match the server for the
    local variant exactly.

    Served to the frontend (e.g. via GET /api/brain/local-config) so client and
    server agree on model id, dim, vector space and preprocessing for the ViT-S
    space. Cross-variant similarity is intentionally not supported (different dims).
    """
    spec = get_model_spec(DEFAULT_LOCAL_MODEL)
    pp = spec.preprocessing
    return {
        "model_name": spec.name.value,
        "onnx_artifact_id": spec.onnx_artifact_id,
        "embedding_dim": spec.embedding_dim,
        "execution_provider": ExecutionProvider.WEBGPU.value,
        "vector_space": spec.vector_space,
        "preprocessing": {
            "size": pp.size,
            "resample": pp.resample,
            "rescale_factor": pp.rescale_factor,
            "image_mean": list(pp.image_mean),
            "image_std": list(pp.image_std),
            "pooling": pp.pooling,
        },
    }
