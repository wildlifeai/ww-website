# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""DINOv3 embedding extractor — infrastructure layer for the Wildlife Brain.

Loads a DINOv3 variant (per ``registries.embedding_registry``) once and extracts
1280-d CLS embeddings from animal crops. Server path; the in-browser WebGPU path
(ViT-S) produces vectors into the *same* 1280-d space (see embedding_registry).

Design (per backend agent skill — services do infra only, no FastAPI):

- ``torch`` / ``transformers`` (large) are imported **lazily** on first model
  load, so this module imports in the lean API image and in tests without them.
- ``chunk`` is pure and unit-testable; the heavy extraction runs off the event
  loop via ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import importlib.util
from io import BytesIO
from typing import Any, Optional, Sequence

import structlog

from app.config import settings
from app.registries.embedding_registry import get_model_spec

logger = structlog.get_logger()


def chunk(seq: Sequence, size: int) -> list[list]:
    """Split a sequence into batches of at most ``size`` (pure helper)."""
    if size <= 0:
        raise ValueError("batch size must be positive")
    return [list(seq[i : i + size]) for i in range(0, len(seq), size)]


# Everything an embedding run imports: DINOv3 itself plus the UMAP and HDBSCAN steps.
_ML_MODULES = ("torch", "transformers", "umap", "hdbscan")


def _missing_ml_modules() -> list[str]:
    return [m for m in _ML_MODULES if importlib.util.find_spec(m) is None]


def _hf_token() -> Optional[str]:
    """``HF_TOKEN``, else the token ``huggingface-cli login`` stored (what from_pretrained falls back to)."""
    if settings.HF_TOKEN:
        return settings.HF_TOKEN
    try:
        from huggingface_hub import get_token

        return get_token()
    except Exception:  # noqa: BLE001, no hub library or no readable token file
        return None


def _cuda_available() -> bool:
    import torch

    return bool(torch.cuda.is_available())


def unavailable_reason(model_name: Optional[str] = None) -> Optional[str]:
    """Why this process can't compute embeddings for ``model_name``, or None when it can.

    Cheap checks run before an embedding run starts, so a process without the ML stack,
    the token for gated weights, or the GPU ``EMBEDDING_DEVICE`` names, skips the run
    instead of downloading every crop and then failing.
    """
    missing = _missing_ml_modules()
    if missing:
        return f"the ML stack is not installed in this image ({', '.join(missing)})"
    spec = get_model_spec(model_name or settings.EMBEDDING_DEFAULT_MODEL)
    if spec.gated and not _hf_token():
        return f"HF_TOKEN is not set and {spec.hf_model_id} is a gated model"
    if settings.EMBEDDING_DEVICE.startswith("cuda") and not _cuda_available():
        return f"EMBEDDING_DEVICE is {settings.EMBEDDING_DEVICE} but no CUDA device is available"
    return None


class DinoV3Service:
    """Lazy-loaded DINOv3 model. Construct once; reuse across requests."""

    def __init__(self, model_name: Optional[str] = None) -> None:
        self.model_name = model_name or settings.EMBEDDING_DEFAULT_MODEL
        self.spec = get_model_spec(self.model_name)
        self.version = self.spec.hf_model_id
        # transformers objects, Any because requirements-ml.txt is not installed where pyright runs.
        self._model: Any = None
        self._processor: Any = None

    def _load(self):
        if self._model is None:
            import torch  # noqa: F401 — heavy, loaded on first use
            from transformers import AutoImageProcessor, AutoModel

            logger.info("dinov3_loading", model=self.spec.hf_model_id, device=settings.EMBEDDING_DEVICE)
            token = settings.HF_TOKEN or None
            self._processor = AutoImageProcessor.from_pretrained(self.spec.hf_model_id, token=token)
            self._model = AutoModel.from_pretrained(self.spec.hf_model_id, token=token)
            self._model.to(settings.EMBEDDING_DEVICE)
            self._model.eval()
        return self._model, self._processor

    def _embed_sync(self, images: list[bytes]) -> list[list[float]]:
        import torch
        from PIL import Image

        model, processor = self._load()
        out: list[list[float]] = []
        for batch in chunk(images, settings.EMBEDDING_BATCH_SIZE):
            pil = [Image.open(BytesIO(b)).convert("RGB") for b in batch]
            inputs = processor(images=pil, return_tensors="pt").to(settings.EMBEDDING_DEVICE)
            with torch.no_grad():
                result = model(**inputs)
            # CLS token (index 0) is the 1280-d image embedding.
            cls = result.last_hidden_state[:, 0, :]
            out.extend(cls.cpu().tolist())
        return out

    async def embed(self, images: Sequence[bytes]) -> list[list[float]]:
        """Extract 1280-d embeddings for a list of image bytes (off the event loop)."""
        if not images:
            return []
        vectors = await asyncio.to_thread(self._embed_sync, list(images))
        # Defensive: each variant has its own fixed dim (1280 ViT-H, 384 ViT-S).
        expected = self.spec.embedding_dim
        for v in vectors:
            if len(v) != expected:
                raise ValueError(f"{self.model_name} produced dim {len(v)}, expected {expected}")
        return vectors


_service: Optional[DinoV3Service] = None


def get_dinov3_service() -> DinoV3Service:
    """Return a process-wide DinoV3Service instance."""
    global _service
    if _service is None:
        _service = DinoV3Service()
    return _service
