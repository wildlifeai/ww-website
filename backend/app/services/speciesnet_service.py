# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""SpeciesNet adapter — detector + classifier ensemble (infrastructure layer).

SpeciesNet (Google) bundles a MegaDetector-family detector with a ~2k-species
classifier. This service loads the model once and returns a normalised,
SpeciesNet-shape-independent result (``ImagePrediction``) that the domain layer
maps to CamtrapDP ``observations`` rows.

Design (per backend agent skill — services do infra only, no FastAPI):

- ``speciesnet`` (torch/tf, large) is imported **lazily** inside the model
  getter so this module loads in the lean API image and in tests without the
  package installed.
- The output parser and taxonomy helpers are pure (no third-party imports), so
  the mapping logic is unit-testable against captured SpeciesNet JSON without a
  GPU or model download.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import structlog

logger = structlog.get_logger()

# Pinned model + provenance string written to observations.source_model_version.
SPECIESNET_MODEL = "kaggle:google/speciesnet/pyTorch/v4.0.1a"
SPECIESNET_VERSION = "speciesnet-v4.0.1a"

# SpeciesNet/MegaDetector detection categories → CamtrapDP observation_type.
_CATEGORY_TO_TYPE = {
    "1": "animal",
    "2": "human",
    "3": "vehicle",
    "animal": "animal",
    "human": "human",
    "vehicle": "vehicle",
}


# ── Normalised result types (no third-party imports) ─────────────────


@dataclass(frozen=True)
class Detection:
    """One detection from the SpeciesNet detector."""

    category: str  # raw SpeciesNet category ("1"/"2"/"3" or label)
    observation_type: str  # mapped: animal | human | vehicle | unknown
    confidence: float
    bbox: Optional[tuple[float, float, float, float]]  # (x, y, w, h) normalised 0-1


@dataclass(frozen=True)
class ImagePrediction:
    """Per-image SpeciesNet result: detections + best species classification."""

    filepath: str
    detections: list[Detection]
    scientific_name: Optional[str]
    common_name: Optional[str]
    classification_score: Optional[float]
    # Full parsed lineage (class/order/family/genus/species/common), enabling
    # confidence-based taxonomic roll-up downstream. Empty when unavailable.
    taxonomy: dict[str, Optional[str]] = field(default_factory=dict)

    @property
    def is_blank(self) -> bool:
        return len(self.detections) == 0


# ── Pure helpers ─────────────────────────────────────────────────────


def category_to_observation_type(category: Any) -> str:
    """Map a SpeciesNet detection category to a CamtrapDP observation_type."""
    return _CATEGORY_TO_TYPE.get(str(category).lower(), "unknown")


def parse_prediction_string(prediction: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Parse a SpeciesNet taxonomy string into (scientific_name, common_name).

    SpeciesNet encodes the classification as a semicolon-delimited taxonomy, e.g.
    ``"<uuid>;mammalia;...;apteryx;mantelli;north island brown kiwi"``. The common
    name is the last field; the scientific name is "<genus> <species>" when both
    are present, otherwise the most specific non-empty rank.
    """
    if not prediction:
        return None, None
    parts = [p.strip() for p in prediction.split(";")]
    # Drop a leading UUID field if present (SpeciesNet prefixes one).
    if parts and ("-" in parts[0] and " " not in parts[0]):
        parts = parts[1:]
    # Drop empties AND SpeciesNet's "no cv result" sentinel (an unclassifiable detection):
    # it fills the genus/species/common fields and otherwise surfaces as the species label
    # "No cv result no cv result". Removing it → the row stays an animal with no species,
    # which the UI shows as "Animal".
    parts = [p for p in parts if p and p.lower() != "no cv result"]
    if not parts:
        return None, None
    common_name = parts[-1] or None
    ranks = parts[:-1] if len(parts) > 1 else parts
    genus = ranks[-2] if len(ranks) >= 2 else None
    species = ranks[-1] if ranks else None
    if genus and species:
        scientific_name = f"{genus} {species}".strip().capitalize()
    else:
        scientific_name = species or genus or None
        if scientific_name:
            scientific_name = scientific_name.capitalize()
    return scientific_name, common_name


# SpeciesNet label format (after the leading UUID): class;order;family;genus;species;common.
# Unknown ranks come through as empty strings, so positional mapping is reliable.
_TAXONOMY_RANKS = ("class", "order", "family", "genus", "species", "common")


def parse_taxonomy(prediction: Optional[str]) -> dict[str, Optional[str]]:
    """Parse a SpeciesNet taxonomy string into a rank → name dict.

    ``"<uuid>;mammalia;apterygiformes;apterygidae;apteryx;mantelli;… kiwi"`` →
    ``{"class": "mammalia", "order": "apterygiformes", "family": "apterygidae",
    "genus": "apteryx", "species": "mantelli", "common": "… kiwi"}``. Missing or
    empty ranks map to ``None``. Returns ``{}`` for an empty prediction.
    """
    if not prediction:
        return {}
    parts = [p.strip() for p in prediction.split(";")]
    # Drop a leading UUID field if present (SpeciesNet prefixes the species GUID).
    # Match the exact UUID shape (36 chars, 4 hyphens) so a hyphenated taxon name
    # in an un-prefixed string can't be mistaken for a UUID and dropped.
    if parts and len(parts[0]) == 36 and parts[0].count("-") == 4:
        parts = parts[1:]
    if not parts:
        return {}
    return {rank: (parts[i].strip() or None) if i < len(parts) else None for i, rank in enumerate(_TAXONOMY_RANKS)}


def _parse_bbox(raw: Any) -> Optional[tuple[float, float, float, float]]:
    """Coerce a SpeciesNet bbox ([x, y, w, h] normalised) into a 4-tuple."""
    if not raw or len(raw) != 4:
        return None
    try:
        x, y, w, h = (float(v) for v in raw)
    except (TypeError, ValueError):
        return None
    return (x, y, w, h)


# Category "0" is the whole-image classifier's pseudo-detection (speciesnet >= 5.0.6):
# a full-frame box that carries the image's classification, not something the detector saw.
_WHOLE_IMAGE_CATEGORY = "0"


def _detection_confidence(det: dict[str, Any]) -> float:
    return float(det.get("conf", det.get("confidence", 0.0)) or 0.0)


def _image_classification(pred: dict[str, Any]) -> tuple[Optional[str], Optional[float]]:
    """Return the image's (taxonomy label, score).

    Each classified detection carries its own ``processed_classification``
    (``class``/``score``). The image keeps a single classification here, so take the
    whole-image pseudo-detection's when there is one, else the highest-confidence
    classified detection's.
    """
    classified = [d for d in pred.get("detections", []) or [] if d.get("processed_classification")]
    if not classified:
        return None, None
    best = max(
        classified,
        key=lambda d: (str(d.get("category")) == _WHOLE_IMAGE_CATEGORY, _detection_confidence(d)),
    )
    processed = best["processed_classification"]
    return processed.get("class"), processed.get("score")


def parse_speciesnet_output(raw: dict[str, Any]) -> list[ImagePrediction]:
    """Convert raw SpeciesNet ``predict`` output into ``ImagePrediction`` list.

    Tolerant of missing fields so a partial/older SpeciesNet response still maps
    cleanly. Detection confidence key is ``conf``; the classification is read from
    the detections by ``_image_classification``. Expects the speciesnet 5.0.6 format
    (``format_version`` 20261015), the version pinned in ``requirements-ml.txt``.
    """
    out: list[ImagePrediction] = []
    for pred in raw.get("predictions", []) or []:
        detections: list[Detection] = []
        for det in pred.get("detections", []) or []:
            category = det.get("category", det.get("label", "unknown"))
            if str(category) == _WHOLE_IMAGE_CATEGORY:
                continue
            detections.append(
                Detection(
                    category=str(category),
                    observation_type=category_to_observation_type(category),
                    confidence=_detection_confidence(det),
                    bbox=_parse_bbox(det.get("bbox")),
                )
            )
        label, score = _image_classification(pred)
        scientific_name, common_name = parse_prediction_string(label)
        out.append(
            ImagePrediction(
                filepath=pred.get("filepath", ""),
                detections=detections,
                scientific_name=scientific_name,
                common_name=common_name,
                classification_score=float(score) if score is not None else None,
                taxonomy=parse_taxonomy(label),
            )
        )
    return out


# ── Service ──────────────────────────────────────────────────────────


class SpeciesNetService:
    """Lazy-loaded SpeciesNet ensemble. Construct once; reuse across requests."""

    def __init__(self, model_name: str = SPECIESNET_MODEL) -> None:
        self.model_name = model_name
        self.version = SPECIESNET_VERSION
        self._model = None  # lazily constructed speciesnet.SpeciesNet
        # Serialises model construction + inference across threads. The torch.fx
        # detector trace is NOT safe to run concurrently on the shared model — two
        # upload batches each auto-annotate the same deployment at once, which
        # double-loads the model and corrupts the trace, raising
        # NameError('module is not installed as a submodule').
        self._lock = threading.Lock()

    def _get_model(self):
        if self._model is None:
            from speciesnet import SpeciesNet  # heavy (torch/tf) — imported on first use

            logger.info("speciesnet_loading", model=self.model_name)
            self._model = SpeciesNet(self.model_name)
        return self._model

    async def predict(self, image_paths: Sequence[str]) -> list[ImagePrediction]:
        """Run the ensemble on local image paths (off the event loop)."""
        if not image_paths:
            return []

        from app.config import settings

        def _run() -> dict[str, Any]:
            # Hold the lock across BOTH construction and inference so concurrent
            # callers serialise rather than racing the detector's torch.fx trace.
            with self._lock:
                model = self._get_model()
                # run_mode='single_thread' additionally avoids SpeciesNet's internal
                # ThreadPool tracing the detector concurrently *within* one predict.
                try:
                    return model.predict(
                        filepaths=list(image_paths),
                        run_mode=settings.SPECIESNET_RUN_MODE,
                        progress_bars=False,
                    )
                except Exception:
                    # A failed/partial torch.fx trace can poison the cached model;
                    # drop it so the next call rebuilds from scratch instead of
                    # failing forever.
                    self._model = None
                    raise

        raw = await asyncio.to_thread(_run)
        return parse_speciesnet_output(raw)


_service: Optional[SpeciesNetService] = None


def get_speciesnet_service() -> SpeciesNetService:
    """Return a process-wide SpeciesNetService instance."""
    global _service
    if _service is None:
        _service = SpeciesNetService()
    return _service
