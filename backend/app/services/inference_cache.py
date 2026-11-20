# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Model outputs computed on the REANNZ cluster, answered in place of the models at ingest.

The cluster (``jobs/batch_infer.py``) runs SpeciesNet, BioCLIP and DINOv3 and writes one
results part per batch of photos: gzipped JSON lines, a header then one record per photo.
Ingest (``jobs/ingest.py``) activates a part with :func:`use`, and while it is active the
three service getters (``get_speciesnet_service``, ``get_bioclip_service``,
``get_dinov3_service``) return the precomputed providers below, so the pipeline steps run
unchanged and write the same rows the in-process path would.

Keys: a SpeciesNet prediction by media id (the step names each temp file ``<media_id>.jpg``),
a BioCLIP label by ``(media_id, bbox)`` (``bbox`` None for the whole frame) and a DINOv3 vector
by media id (the hero crop the website stored). A miss is counted, never invented.

No database, no torch: this module is imported by the API image and the GPU image alike.
"""

from __future__ import annotations

import contextlib
import contextvars
import gzip
import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Iterator, Optional, Sequence

import structlog

from app.services.bioclip_service import CropPrediction
from app.services.speciesnet_service import Detection, ImagePrediction

logger = structlog.get_logger()

# Bumped whenever the record format changes; also in domain/batch_lease.py's manifest.
INFERENCE_CONTRACT = 1
# Boxes round-trip through JSON and Postgres float8; compare them to this many decimals.
BBOX_DECIMALS = 6

BBoxKey = Optional[tuple[float, float, float, float]]


class ContractMismatch(ValueError):
    """A results part written for another contract version, lease or job."""


def bbox_key(bbox: Any) -> BBoxKey:
    """A box as a hashable key, rounded so a JSON or database round trip still matches."""
    if bbox is None:
        return None
    values = tuple(round(float(v), BBOX_DECIMALS) for v in bbox)
    if len(values) != 4:
        raise ValueError(f"bbox needs 4 values, got {len(values)}")
    return values  # type: ignore[return-value]


def media_id_of(path: str) -> str:
    """The media id a pipeline step encoded in a temp file name (``<id>.jpg``)."""
    return os.path.splitext(os.path.basename(path))[0]


# ── the part format ──────────────────────────────────────────────────


def prediction_to_json(pred: ImagePrediction) -> dict:
    data = asdict(pred)
    data.pop("filepath", None)
    return data


def prediction_from_json(data: dict, filepath: str) -> ImagePrediction:
    detections = [
        Detection(
            category=str(d.get("category")),
            observation_type=str(d.get("observation_type")),
            confidence=float(d.get("confidence") or 0.0),
            bbox=tuple(float(v) for v in d["bbox"]) if d.get("bbox") is not None else None,  # type: ignore[arg-type]
        )
        for d in data.get("detections") or []
    ]
    return ImagePrediction(
        filepath=filepath,
        detections=detections,
        scientific_name=data.get("scientific_name"),
        common_name=data.get("common_name"),
        classification_score=data.get("classification_score"),
        taxonomy=dict(data.get("taxonomy") or {}),
    )


def encode_part(header: dict, records: Sequence[dict]) -> bytes:
    """The bytes of one results part: a header line, then one line per photo, gzipped."""
    lines = [json.dumps({"contract": INFERENCE_CONTRACT, **header})] + [json.dumps(r) for r in records]
    return gzip.compress(("\n".join(lines) + "\n").encode("utf-8"))


@dataclass
class ResultsPart:
    """One decoded part, indexed for the providers."""

    header: dict
    records: list[dict]
    speciesnet: dict[str, dict] = field(default_factory=dict)
    bioclip: dict[tuple[str, BBoxKey], dict] = field(default_factory=dict)
    dinov3: dict[str, list[float]] = field(default_factory=dict)
    misses: dict[str, int] = field(default_factory=lambda: {"speciesnet": 0, "bioclip": 0, "dinov3": 0})

    def __post_init__(self) -> None:
        for r in self.records:
            mid = str(r.get("media_id"))
            if r.get("speciesnet") is not None:
                self.speciesnet[mid] = r["speciesnet"]
            for label in r.get("bioclip") or []:
                self.bioclip[(mid, bbox_key(label.get("bbox")))] = label
            if r.get("dinov3") is not None:
                self.dinov3[mid] = [float(v) for v in r["dinov3"]]

    @property
    def media_ids(self) -> list[str]:
        return [str(r.get("media_id")) for r in self.records if not r.get("error")]

    def by_deployment(self) -> dict[str, list[str]]:
        """Media ids of the readable photos, grouped by deployment, in part order."""
        out: dict[str, list[str]] = {}
        for r in self.records:
            if not r.get("error"):
                out.setdefault(str(r.get("deployment_id")), []).append(str(r.get("media_id")))
        return out

    def has_vector(self, media_id: str) -> bool:
        return media_id in self.dinov3

    def merged_with(self, other: "ResultsPart") -> "ResultsPart":
        """One part holding both parts' records (the embed kind needs all of a job's vectors at once)."""
        return ResultsPart(header=self.header, records=self.records + other.records)


def decode_part(data: bytes, *, lease_id: Optional[str] = None, job_id: Optional[str] = None) -> ResultsPart:
    """Parse a part, refusing another contract version, or another lease or job than expected."""
    lines = [line for line in gzip.decompress(data).decode("utf-8").splitlines() if line.strip()]
    if not lines:
        raise ContractMismatch("empty results part")
    header = json.loads(lines[0])
    if header.get("contract") != INFERENCE_CONTRACT:
        raise ContractMismatch(f"results part contract {header.get('contract')!r}, this image reads {INFERENCE_CONTRACT}")
    if lease_id is not None and header.get("lease_id") != lease_id:
        raise ContractMismatch("results part belongs to another lease")
    if job_id is not None and header.get("job_id") != job_id:
        raise ContractMismatch("results part belongs to another job")
    return ResultsPart(header=header, records=[json.loads(line) for line in lines[1:]])


# ── the active part ──────────────────────────────────────────────────

_active: contextvars.ContextVar[Optional[ResultsPart]] = contextvars.ContextVar("ww_inference_part", default=None)


def active() -> Optional[ResultsPart]:
    """The part ingest is running on, or None (the real models answer)."""
    return _active.get()


@contextlib.contextmanager
def use(part: ResultsPart) -> Iterator[ResultsPart]:
    """Answer model calls in this context (and tasks it starts) from ``part``."""
    token = _active.set(part)
    try:
        yield part
    finally:
        _active.reset(token)


# ── precomputed providers ────────────────────────────────────────────


class PrecomputedSpeciesNet:
    """``SpeciesNetService.predict`` from the active part."""

    def __init__(self, part: ResultsPart) -> None:
        from app.services.speciesnet_service import SPECIESNET_VERSION

        self.part = part
        self.version = SPECIESNET_VERSION

    async def predict(self, image_paths: Sequence[str]) -> list[ImagePrediction]:
        out: list[ImagePrediction] = []
        for path in image_paths:
            found = self.part.speciesnet.get(media_id_of(path))
            if found is None:
                self.part.misses["speciesnet"] += 1
                continue
            out.append(prediction_from_json(found, path))
        return out


class PrecomputedBioCLIP:
    """``BioCLIPService.predict`` from the active part, by the keys the step passes."""

    def __init__(self, part: ResultsPart) -> None:
        from app.services.bioclip_service import BIOCLIP_VERSION

        self.part = part
        self.version = BIOCLIP_VERSION

    async def predict(
        self,
        image_paths: Sequence[str],
        labels: Optional[Sequence[str]] = None,
        rank: str = "species",
        keys: Optional[Sequence[tuple[str, Any]]] = None,
    ) -> list[CropPrediction]:
        header_labels = self.part.header.get("bioclip_labels")
        if (list(labels) if labels else None) != (list(header_labels) if header_labels else None) or (
            not labels and rank != self.part.header.get("bioclip_rank", rank)
        ):
            raise ContractMismatch("BioCLIP labels or rank differ from what the cluster ran")
        if keys is None:
            raise ContractMismatch("precomputed BioCLIP needs (media_id, bbox) keys")
        out: list[CropPrediction] = []
        for path, (media_id, bbox) in zip(image_paths, keys):
            found = self.part.bioclip.get((str(media_id), bbox_key(bbox)))
            if found is None:
                self.part.misses["bioclip"] += 1
                continue
            out.append(CropPrediction(path, found.get("scientific_name"), found.get("common_name"), found.get("score"), found.get("rank")))
        return out


class PrecomputedDinoV3:
    """``DinoV3Service.embed`` from the active part, by media id."""

    def __init__(self, part: ResultsPart) -> None:
        from app.config import settings
        from app.registries.embedding_registry import get_model_spec

        self.part = part
        self.model_name = part.header.get("embedding_model") or settings.EMBEDDING_DEFAULT_MODEL
        self.spec = get_model_spec(self.model_name)
        self.version = self.spec.hf_model_id

    async def embed(self, images: Sequence[bytes], keys: Optional[Sequence[str]] = None) -> list[list[float]]:
        if keys is None or len(keys) != len(images):
            raise ContractMismatch("precomputed DINOv3 needs one media id per image")
        out: list[list[float]] = []
        for media_id in keys:
            vector = self.part.dinov3.get(str(media_id))
            if vector is None:
                self.part.misses["dinov3"] += 1
                raise ContractMismatch(f"no precomputed vector for media {media_id}")
            if len(vector) != self.spec.embedding_dim:
                raise ContractMismatch(f"vector of {len(vector)} values, {self.model_name} has {self.spec.embedding_dim}")
            out.append(vector)
        return out


def precomputed_speciesnet() -> Optional[PrecomputedSpeciesNet]:
    part = active()
    return PrecomputedSpeciesNet(part) if part is not None else None


def precomputed_bioclip() -> Optional[PrecomputedBioCLIP]:
    part = active()
    return PrecomputedBioCLIP(part) if part is not None else None


def precomputed_dinov3() -> Optional[PrecomputedDinoV3]:
    part = active()
    return PrecomputedDinoV3(part) if part is not None else None
