# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""BioCLIP adapter — zero-shot / secondary species classifier (infra layer).

BioCLIP (Imageomics) is a CLIP model trained on TreeOfLife-10M that aligns
images with the biological taxonomy. Unlike SpeciesNet it has **no detector and
no blank-frame handling**, so it is used here as a *secondary* classifier that
runs on the animal crops SpeciesNet + AnimalCrop already produced. It shines on
taxa outside SpeciesNet's ~2k label set (invertebrates, plants, regional
species) and as a second opinion that flags disagreements for human review.

Two modes (chosen by the domain layer):

- **Tree-of-Life**   — open-ended prediction across the whole taxonomy
                       (``TreeOfLifeClassifier``), ranked to a chosen ``Rank``.
- **Custom labels**  — constrain to a project's species list
                       (``CustomLabelsClassifier``), no retraining required.

Design (per backend agent skill — services do infra only, no FastAPI):

- ``bioclip`` (open_clip / torch, large) is imported **lazily** inside the model
  getter so this module loads in the lean API image and in tests without the
  package installed.
- The output parser (``parse_bioclip_output``) is pure (no third-party imports)
  and unit-testable against captured pybioclip rows without a GPU or download.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import structlog

logger = structlog.get_logger()

# Provenance string written to observations.source_model_version.
BIOCLIP_MODEL = "imageomics/bioclip-2"
BIOCLIP_VERSION = "bioclip-2"

# Standard Linnaean ranks pybioclip emits, coarse → fine.
_RANK_ORDER = ("kingdom", "phylum", "class", "order", "family", "genus", "species")


# ── Normalised result type (no third-party imports) ──────────────────


@dataclass(frozen=True)
class CropPrediction:
    """Per-image BioCLIP result: the single best-scoring taxon."""

    filepath: str
    scientific_name: Optional[str]
    common_name: Optional[str]
    score: Optional[float]
    rank: Optional[str]


# ── Pure helpers ─────────────────────────────────────────────────────


def _extract_scientific_name(row: dict[str, Any]) -> Optional[str]:
    """Build a binomial scientific name from a pybioclip prediction row.

    Tolerant of both shapes pybioclip emits:
    - Tree-of-Life rows carry per-rank columns (``genus``, ``species``…).
    - Custom-label rows carry a single ``classification`` string.
    Prefers ``genus`` + ``species`` when present, else the most specific
    non-empty rank, else the raw classification/scientific_name field.
    """
    genus = (row.get("genus") or "").strip()
    species = (row.get("species") or "").strip()
    if genus and species:
        # ``species`` may already be the binomial ("Ardea cinerea") or just the
        # epithet ("vulpes"). A space means it's already a binomial.
        if " " in species:
            return species[0].upper() + species[1:]
        return f"{genus} {species}".strip().capitalize()

    for rank in reversed(_RANK_ORDER):
        val = (row.get(rank) or "").strip()
        if val:
            return val.capitalize()

    for key in ("scientific_name", "classification", "name"):
        val = (row.get(key) or "").strip()
        if val:
            return val
    return None


def _score_of(row: dict[str, Any]) -> float:
    for key in ("score", "prediction_score", "probability", "confidence"):
        if row.get(key) is not None:
            try:
                return float(row[key])
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def parse_bioclip_output(rows: Sequence[dict[str, Any]], rank: Optional[str] = None) -> list[CropPrediction]:
    """Convert raw pybioclip prediction rows into ``CropPrediction`` list.

    pybioclip returns one row per (image, candidate) pair, each with a
    ``file_name`` and a ``score``. We keep the single highest-scoring candidate
    per image so the domain layer writes one observation per crop.
    """
    best: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        fp = row.get("file_name") or row.get("filepath") or row.get("filename") or ""
        if not fp:
            continue
        if fp not in best or _score_of(row) > _score_of(best[fp]):
            best[fp] = row

    out: list[CropPrediction] = []
    for fp, row in best.items():
        out.append(
            CropPrediction(
                filepath=fp,
                scientific_name=_extract_scientific_name(row),
                common_name=(row.get("common_name") or row.get("vernacular_name") or None),
                score=_score_of(row),
                rank=rank or row.get("rank"),
            )
        )
    return out


# ── Service ──────────────────────────────────────────────────────────


class BioCLIPService:
    """Lazy-loaded BioCLIP classifier. Construct once; reuse across requests."""

    def __init__(self, device: Optional[str] = None) -> None:
        self.version = BIOCLIP_VERSION
        self._device = device
        self._tol_model = None  # bioclip.TreeOfLifeClassifier
        self._custom_models: dict[tuple[str, ...], Any] = {}

    def _resolve_device(self) -> str:
        if self._device:
            return self._device
        from app.config import settings

        return settings.BIOCLIP_DEVICE or "cpu"

    def _get_tol_model(self):
        if self._tol_model is None:
            from bioclip import TreeOfLifeClassifier  # heavy (torch/open_clip)

            logger.info("bioclip_loading", model="tree_of_life", device=self._resolve_device())
            self._tol_model = TreeOfLifeClassifier(device=self._resolve_device())
        return self._tol_model

    def _get_custom_model(self, labels: Sequence[str]):
        key = tuple(sorted(labels))
        if key not in self._custom_models:
            from bioclip import CustomLabelsClassifier  # heavy

            logger.info("bioclip_loading", model="custom_labels", labels=len(key))
            self._custom_models[key] = CustomLabelsClassifier(list(labels), device=self._resolve_device())
        return self._custom_models[key]

    async def predict(
        self,
        image_paths: Sequence[str],
        labels: Optional[Sequence[str]] = None,
        rank: str = "species",
        keys: Optional[Sequence[Any]] = None,
    ) -> list[CropPrediction]:
        """Classify local image (crop) paths off the event loop.

        ``keys`` (``(media_id, bbox)`` per path) is for the precomputed provider of the
        REANNZ batch (``services/inference_cache.py``); the model itself ignores it.

        - ``labels`` given  → constrain to that label set (CustomLabelsClassifier).
        - ``labels`` absent → open Tree-of-Life prediction at ``rank``.
        """
        if not image_paths:
            return []

        def _run() -> list[dict[str, Any]]:
            paths = list(image_paths)
            if labels:
                model = self._get_custom_model(labels)
                return list(model.predict(paths))
            from bioclip import Rank  # enum: Rank.SPECIES, Rank.GENUS, …

            model = self._get_tol_model()
            rank_enum = getattr(Rank, rank.upper(), Rank.SPECIES)
            return list(model.predict(paths, rank_enum))

        rows = await asyncio.to_thread(_run)
        return parse_bioclip_output(rows, rank=None if labels else rank)


_service: Optional[BioCLIPService] = None


def get_bioclip_service() -> Any:
    """Return a process-wide BioCLIPService instance, or the cluster's results while ingest runs one."""
    from app.services.inference_cache import precomputed_bioclip

    precomputed = precomputed_bioclip()
    if precomputed is not None:
        return precomputed
    global _service
    if _service is None:
        _service = BioCLIPService()
    return _service
