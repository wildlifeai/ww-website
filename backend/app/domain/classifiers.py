# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pluggable species classifiers — the "Classify" stage of the inference tree.

The pipeline is a decision tree: **detect** (where are the animals?) → **crop** →
**classify** (what species?). This module owns the last stage as a swappable
contract so a project can route its animal crops to a custom species model
(e.g. a gecko or wētā classifier) instead of the default, falling back to the
general model when none is configured.

A ``Classifier`` takes animal-crop image paths and returns one
``ClassifierResult`` per path. Implementations live behind a registry and are
resolved by id at run time; the heavy ML deps are imported lazily inside
``classify`` so this module (and its tests) load without torch/pybioclip.

Detection is still handled by the SpeciesNet ensemble (see
``services/speciesnet_service.py``); a parallel ``Detector`` contract is the
documented next step — see ``docs … 04-AI-PIPELINE``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional

import structlog

logger = structlog.get_logger()


# ── Normalised result (no third-party imports) ───────────────────────


@dataclass(frozen=True)
class ClassifierResult:
    """One classifier prediction for a single crop.

    Field-compatible with ``services.bioclip_service.CropPrediction`` so the
    existing observation builder consumes either without changes.
    """

    filepath: str
    scientific_name: Optional[str]
    common_name: Optional[str]
    score: Optional[float]
    rank: Optional[str] = None


# ── Contract ─────────────────────────────────────────────────────────


class Classifier(ABC):
    """A species classifier that labels animal crops."""

    #: Stable registry id (e.g. ``"bioclip"``). Also the routing key.
    name: str

    @property
    @abstractmethod
    def version(self) -> str:
        """Provenance string written to ``observations.source_model_version``."""
        ...

    @abstractmethod
    async def classify(self, crop_paths: list[str], config: dict, keys: Optional[list] = None) -> list[ClassifierResult]:
        """Classify each crop path; returns one result per resolvable path.

        ``keys`` names each crop as ``(media_id, bbox)``, ``bbox`` None for a whole frame. A
        model ignores it; the REANNZ batch's precomputed answers are looked up by it.
        """
        ...


# ── Registry + routing ───────────────────────────────────────────────

_REGISTRY: dict[str, Callable[[], Classifier]] = {}

DEFAULT_CLASSIFIER = "bioclip"


def register_classifier(name: str, factory: Callable[[], Classifier]) -> None:
    """Register a classifier factory under *name* (idempotent overwrite)."""
    _REGISTRY[name] = factory


def available_classifiers() -> list[str]:
    """Sorted list of registered classifier ids."""
    return sorted(_REGISTRY)


def resolve_classifier(name: str) -> Classifier:
    """Instantiate a classifier by id.

    Raises:
        ValueError: when *name* is not registered (lists the valid ids).
    """
    factory = _REGISTRY.get(name)
    if factory is None:
        valid = ", ".join(available_classifiers()) or "(none)"
        raise ValueError(f"Unknown classifier '{name}'. Registered: {valid}")
    return factory()


def resolve_classifier_name(config: Optional[dict] = None, project_classifier: Optional[str] = None) -> str:
    """Pick the classifier id by precedence: run config → project setting → default.

    Pure (no I/O) so routing is unit-testable. ``project_classifier`` is the seam
    for a future per-project column; today only the ``config['classifier']``
    override and the default are wired.
    """
    if config and config.get("classifier"):
        return str(config["classifier"])
    if project_classifier:
        return project_classifier
    return DEFAULT_CLASSIFIER


# ── BioCLIP reference implementation ─────────────────────────────────


class BioClipClassifier(Classifier):
    """Default classifier — Imageomics BioCLIP, run on animal crops.

    Honours the existing per-run overrides ``bioclip_labels`` (constrain to a
    custom label set — the lightweight path to a project-specific classifier)
    and ``bioclip_rank`` (Tree-of-Life rank).
    """

    name = "bioclip"

    @property
    def version(self) -> str:
        from app.services.bioclip_service import BIOCLIP_VERSION

        return BIOCLIP_VERSION

    async def classify(self, crop_paths: list[str], config: dict, keys: Optional[list] = None) -> list[ClassifierResult]:
        from app.config import settings
        from app.services.bioclip_service import get_bioclip_service

        labels = config.get("bioclip_labels")
        rank = config.get("bioclip_rank", settings.BIOCLIP_RANK)
        if keys is None:
            preds = await get_bioclip_service().predict(crop_paths, labels=labels, rank=rank)
        else:
            preds = await get_bioclip_service().predict(crop_paths, labels=labels, rank=rank, keys=keys)
        return [ClassifierResult(p.filepath, p.scientific_name, p.common_name, p.score, p.rank) for p in preds]


register_classifier(BioClipClassifier.name, BioClipClassifier)
