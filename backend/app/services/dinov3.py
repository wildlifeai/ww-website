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
