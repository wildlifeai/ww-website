# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Species Brain training domain: from an Annotations selection to a camera model.

The Edge Impulse recipe the team used by hand for the rat classifier (see the
"Machine Learning Models" guide): crop or frame per labelled image, 96×96
grayscale, fit-shortest resize, MobileNetV2 0.35 transfer learning, 30 epochs at
0.001 with augmentation, int8 export, then the website's Vela conversion. This
module automates the dataset half and orchestrates the rest:

    selected media ──► samples per (media, observation) with a class each
                  ──► image bytes (observation crop › animal crop › full frame)
                  ──► trainer (services/edge_impulse.py) ──► int8 ZIP
                  ──► convert_uploaded_model (Vela) ──► .TFL + labels.txt
                  ──► ai_models row: validated, detection_capabilities, label_map

Without Edge Impulse credentials the same dataset is packaged as an
Edge-Impulse-ready ZIP (``training/<label>/<label>.<n>.jpg`` …) so a person can
train it by hand (the export-only mode).

Pure functions (``assign_samples``, ``split_samples``, ``sanitize_label``,
``default_background_label``, ``build_dataset_zip``) carry the rules and are unit
tested; the async functions do I/O.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import structlog

from app.config import settings
from app.schemas.model import TrainModelRequest

logger = structlog.get_logger()

# Observation columns the dataset builder needs, one PostgREST select shared by the
# fetch and the tests' fixtures.
MEDIA_SELECT = (
    "id, deployment_id, file_path, media_assets(animal_crop_url), "
    "observations(id, observation_type, scientific_name, crop_url, ai_origin, source_type, "
    "reviewer_id, annotator_id, classification_probability)"
)

BACKGROUND_ROLE = "background"
TARGET_ROLE = "target"
TEST_FRACTION = 0.2
FULL_FRAME_MAX_PX = 320  # a full frame is downscaled before upload; the model input is 96 or 160 px

ProgressFn = Callable[[float, str], Awaitable[None]]


class TrainingError(Exception):
    """A dataset or training problem the user can act on (surfaced as the job error)."""


@dataclass(frozen=True)
class TrainingSample:
    """One image the model learns from, with the class it belongs to."""

    sample_id: str  # observation id, or "<media_id>:blank" for a media-level blank
    media_id: str
    deployment_id: Optional[str]
    label: str  # device class label
    role: str  # TARGET_ROLE | BACKGROUND_ROLE
    image_ref: str  # crop_url / animal_crop_url / file_path, resolved by resolve_media
    is_full_frame: bool  # full frames are downscaled before upload


@dataclass
class DatasetSummary:
    """What the selection turned into, for the job log and the UI."""

    labels: List[str]  # class order requested: background first, then targets
    counts: Dict[str, int] = field(default_factory=dict)
    skipped_unlabelled: int = 0  # media with no usable (non-edge) observation
    skipped_unlisted: int = 0  # observations of species not in the class list (background off)
    edge_only_ignored: int = 0  # media whose only observations came from the camera itself

    def as_dict(self) -> Dict[str, Any]:
        return {
            "labels": list(self.labels),
            "counts": dict(self.counts),
            "total": sum(self.counts.values()),
            "skipped_unlabelled": self.skipped_unlabelled,
            "skipped_unlisted": self.skipped_unlisted,
            "edge_only_ignored": self.edge_only_ignored,
        }


# ── Pure rules ────────────────────────────────────────────────────────


def sanitize_label(raw: str) -> str:
    """A device- and Edge-Impulse-safe class label.

    Lowercase letters, digits, spaces, ``_`` and ``-`` only; whitespace collapsed;
    at most 32 characters. Mirrors the existing labels ("not rat", "rat",
    "no person"): spaces are allowed because the device reads ``labels.txt``
    line by line.
    """
    s = re.sub(r"[^a-z0-9 _-]+", " ", (raw or "").strip().lower())
    s = re.sub(r"\s+", " ", s).strip(" _-")
    return s[:32].rstrip(" _-")


def label_slug(label: str) -> str:
    """Filename-safe form of a label (``not rat`` → ``not_rat``) for the dataset ZIP."""
    return re.sub(r"[^a-z0-9_-]+", "_", sanitize_label(label)).strip("_") or "class"


def default_background_label(target_labels: List[str]) -> str:
    """Pick a background label that sorts *before* the targets.

    Edge Impulse orders a model's output classes alphabetically, and the firmware
    reports class index 1 as "the target" for a two-class model. ``not gecko``
    would sort after ``gecko`` and make the negative class the target, so the
    default only uses ``not <target>`` when it sorts first, then ``background``,
    and finally ``_background`` (an underscore sorts before every lowercase letter).
    """
    targets = sorted(t for t in target_labels if t)
    if not targets:
        return "background"
    first = targets[0]
    candidates = ([f"not {first}"] if len(target_labels) == 1 else []) + ["other", "background", "_background"]
    for candidate in candidates:
        if candidate < first and candidate not in target_labels:
            return candidate
    return "_background"


def _is_human(obs: dict) -> bool:
    return bool(obs.get("reviewer_id") or obs.get("annotator_id") or obs.get("source_type") == "human")


def assign_samples(
    media_rows: List[dict],
    classes: List[dict],
    *,
    include_background: bool,
    background_label: str,
) -> Tuple[List[TrainingSample], DatasetSummary]:
    """Turn media rows (with nested observations and assets) into labelled samples.

    Rules, in order:
    - Observations the camera itself produced (``ai_origin == 'edge'``) are never
      training data: the model would learn the mistakes of its own predecessor.
    - When a media has any human observation, only human observations count;
      otherwise the Cloud AI ones do.
    - A ``blank`` observation makes one background sample of the frame.
    - An observation whose ``scientific_name`` matches a class becomes a sample of
      that class, cropped to the observation's own crop when it has one.
    - Anything else is background when ``include_background`` is on, else skipped.

    ``classes`` items carry ``label`` (device label) and ``scientific_name``.
    """
    by_name: Dict[str, str] = {}
    for c in classes:
        name = (c.get("scientific_name") or "").strip().lower()
        if name:
            by_name[name] = sanitize_label(c["label"])
    target_labels = list(dict.fromkeys(by_name.values()))
    bg = sanitize_label(background_label) if background_label else default_background_label(target_labels)

    ordered_labels = ([bg] if include_background else []) + target_labels
    summary = DatasetSummary(labels=ordered_labels, counts={lbl: 0 for lbl in ordered_labels})
    samples: List[TrainingSample] = []
    seen: set[str] = set()

    def add(sample: TrainingSample) -> None:
        if sample.sample_id in seen:
            return
        seen.add(sample.sample_id)
        samples.append(sample)
        summary.counts[sample.label] = summary.counts.get(sample.label, 0) + 1

    for m in media_rows:
        media_id = m["id"]
        assets = m.get("media_assets")
        if isinstance(assets, list):
            assets = assets[0] if assets else None
        animal_crop = (assets or {}).get("animal_crop_url")
        frame_ref = m.get("file_path") or ""

        observations = [o for o in (m.get("observations") or []) if o.get("ai_origin") != "edge"]
        if not observations:
            if m.get("observations"):
                summary.edge_only_ignored += 1
            else:
                summary.skipped_unlabelled += 1
            continue
        humans = [o for o in observations if _is_human(o)]
        chosen = humans or observations

        for o in chosen:
            if o.get("observation_type") == "blank":
                if include_background and frame_ref:
                    add(TrainingSample(f"{media_id}:blank", media_id, m.get("deployment_id"), bg, BACKGROUND_ROLE, frame_ref, True))
                continue
            name = (o.get("scientific_name") or "").strip().lower()
            label = by_name.get(name)
            crop = o.get("crop_url") or animal_crop
            ref = crop or frame_ref
            if not ref:
                continue
            if label:
                add(TrainingSample(o["id"], media_id, m.get("deployment_id"), label, TARGET_ROLE, ref, crop is None))
            elif include_background:
                add(TrainingSample(o["id"], media_id, m.get("deployment_id"), bg, BACKGROUND_ROLE, ref, crop is None))
            else:
                summary.skipped_unlisted += 1

    return samples, summary


def validate_dataset(summary: DatasetSummary, *, min_per_class: int, max_images: int, max_classes: int) -> None:
    """Refuse datasets the device or the recipe cannot use, with a message a user can act on."""
    labels = [lbl for lbl in summary.labels if summary.counts.get(lbl, 0) > 0]
    if len(summary.labels) > max_classes:
        raise TrainingError(f"Too many classes ({len(summary.labels)}); the camera supports at most {max_classes} including background.")
    if len(labels) < 2:
        raise TrainingError(
            "A classifier needs at least two classes with images. Select images of the target species "
            "and some blanks or other species for the background class."
        )
    thin = [f"{lbl} ({summary.counts.get(lbl, 0)})" for lbl in summary.labels if summary.counts.get(lbl, 0) < min_per_class]
    if thin:
        raise TrainingError(f"Each class needs at least {min_per_class} images; too few for: {', '.join(thin)}.")
    total = sum(summary.counts.values())
    if total > max_images:
        raise TrainingError(f"{total} images selected; the limit for one training run is {max_images}.")


def split_samples(samples: List[TrainingSample], test_fraction: float = TEST_FRACTION) -> Tuple[List[TrainingSample], List[TrainingSample]]:
    """Deterministic train/test split keyed on the sample id (stable across re-runs).

    Every class keeps at least one training sample; a class with a single image is
    never sent entirely to the test set.
    """
    train: List[TrainingSample] = []
    test: List[TrainingSample] = []
    for s in samples:
        bucket = int(hashlib.sha1(s.sample_id.encode()).hexdigest()[:8], 16) % 100
        (test if bucket < int(test_fraction * 100) else train).append(s)
    trained_labels = {s.label for s in train}
    for s in list(test):
        if s.label not in trained_labels:
            test.remove(s)
            train.append(s)
            trained_labels.add(s.label)
    return train, test


def build_dataset_zip(
    train: List[Tuple[TrainingSample, bytes]],
    test: List[Tuple[TrainingSample, bytes]],
    summary: DatasetSummary,
    *,
    model_name: str,
    request: Dict[str, Any],
) -> bytes:
    """An Edge-Impulse-ready dataset: ``training/<slug>/<slug>.<n>.jpg`` + ``testing/…``.

    Filenames follow Edge Impulse's "infer label from filename" convention
    (``label.index.jpg``) and avoid spaces, which its uploader rejects.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for split_name, rows in (("training", train), ("testing", test)):
            per_label: Dict[str, int] = {}
            for sample, data in rows:
                slug = label_slug(sample.label)
                n = per_label.get(slug, 0) + 1
                per_label[slug] = n
                z.writestr(f"{split_name}/{slug}/{slug}.{n:04d}.jpg", data)
        manifest = {
            "model_name": model_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "recipe": {
                "image_size": request.get("image_size", 96),
                "colour": request.get("colour", "grayscale"),
                "resize_mode": "fit-short",
                "learning_block": "transfer learning (MobileNetV2 0.35)",
                "epochs": request.get("epochs", 30),
                "learning_rate": request.get("learning_rate", 0.001),
                "augmentation": "all",
                "validation_split": 0.2,
                "export": "Custom → Quantized (int8)",
            },
            "classes": [{"label": lbl, "count": summary.counts.get(lbl, 0)} for lbl in summary.labels],
            "summary": summary.as_dict(),
        }
        z.writestr("dataset.json", json.dumps(manifest, indent=2))
        z.writestr(
            "README.txt",
            "Wildlife Watcher training dataset (Edge Impulse-ready)\n\n"
            "1. edgeimpulse.com → your project → Data acquisition → Add existing data → Upload.\n"
            "2. Upload the folders under training/ and testing/ into the matching category with\n"
            "   'Infer from filename' so each file's label comes from its name (label.index.jpg).\n"
            "3. Create impulse: Image (size in dataset.json, fit-short) → Image (grayscale) →\n"
            "   Transfer learning; train with the settings in dataset.json.\n"
            "4. Deployment → Custom → Quantized (int8) → Build, then upload the ZIP on the\n"
            "   website's Toolkit → Upload model page.\n",
        )
    return buf.getvalue()


def build_label_map(classes: List[dict], labels: List[str], background_label: str) -> Dict[str, dict]:
    """``ai_models.label_map`` for the trained model, keyed by device label.

    Targets carry the taxon the class was built from, so edge reflection can turn
    ``rat: 87%`` into a real observation; the background class is marked as such.
    This is the one path where the mapping is known at creation time instead of
    being asked from the uploader afterwards.
    """
    by_label: Dict[str, dict] = {}
    for c in classes:
        by_label[sanitize_label(c["label"])] = {
            "role": TARGET_ROLE,
            "taxon_id": c.get("taxon_id"),
            "scientific_name": c.get("scientific_name"),
            "vernacular_name": c.get("vernacular_name"),
        }
    out: Dict[str, dict] = {}
    for lbl in labels:
        if lbl in by_label:
            out[lbl] = by_label[lbl]
        else:
            out[lbl] = {"role": BACKGROUND_ROLE, "taxon_id": None, "scientific_name": None, "vernacular_name": None}
    _ = background_label  # the background label is whatever the model reports; kept for symmetry with the request
    return out


def firmware_target_warning(labels: List[str], label_map: Dict[str, dict]) -> Optional[str]:
    """The firmware's two-class convention: class index 1 is 'the target'.

    Returns a warning when a two-class model ends up with the background at index
    1 (alphabetical ordering in Edge Impulse decides the index), or None.
    """
    if len(labels) == 2 and label_map.get(labels[1], {}).get("role") == BACKGROUND_ROLE:
        return (
            f"Class order is {labels}: the camera treats index 1 ('{labels[1]}') as the target for alerts. "
            "Rename the background class so it sorts first, or ignore if you do not use LoRaWAN alerts."
        )
    return None


# ── I/O ───────────────────────────────────────────────────────────────


async def fetch_media_rows(client, media_ids: List[str], *, chunk: int = 200) -> List[dict]:
    """Media with nested assets and observations, in chunks (PostgREST URL length)."""
    rows: List[dict] = []
    for i in range(0, len(media_ids), chunk):
        ids = media_ids[i : i + chunk]
        query = client.table("media").select(MEDIA_SELECT).in_("id", ids).is_("deleted_at", "null")
        res = await asyncio.to_thread(query.execute)
        rows.extend(res.data or [])
    return rows


async def media_deployments(client, media_ids: List[str], *, chunk: int = 200) -> Dict[str, str]:
    """``{media_id: deployment_id}`` for the ids that exist (and are not soft-deleted).

    The router uses it to check the caller may read every selected image before a
    job is queued: access is decided per deployment (``app.authz``), so this is the
    cheapest lookup that gives the deployments a selection spans.
    """
    out: Dict[str, str] = {}
    for i in range(0, len(media_ids), chunk):
        ids = media_ids[i : i + chunk]
        query = client.table("media").select("id, deployment_id").in_("id", ids).is_("deleted_at", "null")
        res = await asyncio.to_thread(query.execute)
        for row in res.data or []:
            if row.get("id") and row.get("deployment_id"):
                out[row["id"]] = row["deployment_id"]
    return out


async def resolve_sample_images(samples: List[TrainingSample], *, concurrency: int = 8) -> List[Tuple[TrainingSample, bytes]]:
    """Fetch bytes for every sample (crops as-is, full frames downscaled). Failures are dropped and logged."""
    from app.domain.media_resolver import resolve_media
    from app.services.image_processing import resize_to_max

    sem = asyncio.Semaphore(concurrency)

    async def _one(s: TrainingSample):
        async with sem:
            try:
                resolved = await resolve_media(s.image_ref, size="full")
            except Exception as exc:  # noqa: BLE001 one bad image must not sink the run
                logger.warning("training_sample_resolve_failed", sample=s.sample_id, error=str(exc))
                return None
        if not resolved:
            return None
        data = resolved[0]
        if s.is_full_frame:
            try:
                data = await asyncio.to_thread(resize_to_max, data, FULL_FRAME_MAX_PX)
            except Exception as exc:  # noqa: BLE001
                logger.warning("training_sample_resize_failed", sample=s.sample_id, error=str(exc))
                return None
        return (s, data)

    results = await asyncio.gather(*[_one(s) for s in samples])
    return [r for r in results if r is not None]


def training_mode() -> str:
    """'edge_impulse' when credentials are configured, else 'export_only'."""
    return "edge_impulse" if settings.EDGE_IMPULSE_API_KEY and settings.EDGE_IMPULSE_PROJECT_ID else "export_only"


def training_status() -> Dict[str, Any]:
    """What the UI needs to decide how to present the action."""
    return {
        "enabled": settings.FF_MODEL_TRAINING_ENABLED,
        "mode": training_mode(),
        "min_images_per_class": settings.MODEL_TRAINING_MIN_IMAGES_PER_CLASS,
        "recommended_images_per_class": settings.MODEL_TRAINING_RECOMMENDED_IMAGES_PER_CLASS,
        "max_images": settings.MODEL_TRAINING_MAX_IMAGES,
        "max_classes": settings.MODEL_TRAINING_MAX_CLASSES,
        "image_sizes": [96, 160],
        "recipe": {
            "learning_block": settings.EDGE_IMPULSE_TRANSFER_MODEL,
            "epochs": 30,
            "learning_rate": 0.001,
            "augmentation": True,
            "validation_split": TEST_FRACTION,
        },
    }


async def build_training_dataset(client, req: TrainModelRequest, progress: Optional[ProgressFn] = None):
    """Selection → validated, resolved dataset. Returns (train, test, summary)."""
    rows = await fetch_media_rows(client, req.media_ids)
    classes = [c.model_dump() for c in req.classes]
    samples, summary = assign_samples(rows, classes, include_background=req.include_background, background_label=req.background_label)
    validate_dataset(
        summary,
        min_per_class=settings.MODEL_TRAINING_MIN_IMAGES_PER_CLASS,
        max_images=settings.MODEL_TRAINING_MAX_IMAGES,
        max_classes=settings.MODEL_TRAINING_MAX_CLASSES,
    )
    if progress:
        await progress(0.15, f"Dataset: {sum(summary.counts.values())} images across {len(summary.labels)} classes; fetching images…")
    resolved = await resolve_sample_images(samples)
    if len(resolved) < len(samples):
        logger.warning("training_samples_unresolved", requested=len(samples), resolved=len(resolved))
    resolved_ids = {s.sample_id for s, _ in resolved}
    summary.counts = {lbl: 0 for lbl in summary.labels}
    for s, _ in resolved:
        summary.counts[s.label] = summary.counts.get(s.label, 0) + 1
    validate_dataset(
        summary,
        min_per_class=settings.MODEL_TRAINING_MIN_IMAGES_PER_CLASS,
        max_images=settings.MODEL_TRAINING_MAX_IMAGES,
        max_classes=settings.MODEL_TRAINING_MAX_CLASSES,
    )
    by_id = {s.sample_id: data for s, data in resolved}
    train_s, test_s = split_samples([s for s in samples if s.sample_id in resolved_ids])
    train = [(s, by_id[s.sample_id]) for s in train_s]
    test = [(s, by_id[s.sample_id]) for s in test_s]
    return train, test, summary
