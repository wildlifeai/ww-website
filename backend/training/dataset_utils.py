# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure helpers for the trainer: configuration, the dataset manifest, class weights, crop geometry.

No TensorFlow here, so these are unit tested on any machine (``tests/``).

The input folder (``TRAINING_INPUT_URI`` or ``--input``) holds ``manifest.json`` and the
images it lists. The website writes it (``app/domain/trainer.py::build_dataset_objects``):

    {"schema_version": 1, "run_key": "...", "model_name": "...",
     "labels": ["not rat", "rat"],                      # class order = output index order
     "recipe": {"image_size": 96, "colour": "grayscale", "epochs": 30, "learning_rate": 0.001},
     "items": [{"file": "images/00000.jpg", "label": "rat", "split": "train"}, ...]}

The split is the website's (#151's ``split_samples``); the trainer never re-splits.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SPLITS = ("train", "test")
MAX_CLASSES = 16
SUPPORTED_SCHEMA = 1


class DatasetError(Exception):
    """The input cannot be trained on; the message says why."""


@dataclass
class Config:
    input_uri: str
    output_uri: str
    run_key: str = "local"
    model_name: str = "species-brain"
    # From the manifest's recipe (the user's choices in #151's modal)
    image_size: int = 96
    colour: str = "grayscale"
    epochs: int = 30
    learning_rate: float = 0.001
    # Owned by the container (the report's recipe table)
    alpha: float = 0.35
    augmentation: bool = True
    class_weights: bool = True
    representative_samples: int = 200
    seed: int = 42
    batch_size: int = 32
    pretrained: bool = True

    @property
    def channels(self) -> int:
        return 3 if self.colour == "rgb" else 1

    def validate(self) -> None:
        if self.image_size not in (96, 160):
            raise DatasetError(f"image_size must be 96 or 160, got {self.image_size}")
        if self.colour not in ("grayscale", "rgb"):
            raise DatasetError(f"colour must be grayscale or rgb, got {self.colour!r}")
        if self.epochs < 1:
            raise DatasetError("epochs must be at least 1")
        if not 0 < self.learning_rate <= 0.1:
            raise DatasetError("learning_rate must be in (0, 0.1]")

    def recipe_dict(self) -> Dict[str, object]:
        keys = ("image_size", "colour", "epochs", "learning_rate", "alpha", "augmentation", "class_weights")
        keys += ("representative_samples", "seed", "batch_size", "pretrained")
        return {k: getattr(self, k) for k in keys}


def read_manifest(root: Path) -> Dict[str, Any]:
    path = Path(root) / "manifest.json"
    if not path.is_file():
        raise DatasetError(f"{path} does not exist; the trainer needs the website's manifest")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise DatasetError(f"manifest.json is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != SUPPORTED_SCHEMA:
        raise DatasetError(f"manifest.json must be an object with schema_version {SUPPORTED_SCHEMA}")
    return data


def config_from(env: Dict[str, str], manifest: Dict[str, Any], overrides: Optional[Dict[str, object]] = None) -> Config:
    """URIs from ``TRAINING_INPUT_URI`` / ``TRAINING_OUTPUT_URI``, the recipe from the manifest, then CLI overrides."""
    recipe = manifest.get("recipe") or {}
    cfg = Config(
        input_uri=env.get("TRAINING_INPUT_URI", "/data/input"),
        output_uri=env.get("TRAINING_OUTPUT_URI", "/data/output"),
        run_key=str(manifest.get("run_key") or "local"),
        model_name=str(manifest.get("model_name") or "species-brain"),
        image_size=int(recipe.get("image_size", 96)),
        colour=str(recipe.get("colour", "grayscale")),
        epochs=int(recipe.get("epochs", 30)),
        learning_rate=float(recipe.get("learning_rate", 0.001)),
    )
    for key, value in (overrides or {}).items():
        if value is not None:
            setattr(cfg, key, value)
    cfg.validate()
    return cfg


# ── Layout ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Item:
    path: Path
    label_index: int
    split: str


@dataclass
class Layout:
    labels: List[str]
    items: List[Item] = field(default_factory=list)

    def counts(self) -> Dict[str, Dict[str, int]]:
        out = {split: {lbl: 0 for lbl in self.labels} for split in SPLITS}
        for it in self.items:
            out[it.split][self.labels[it.label_index]] += 1
        return out

    def by_split(self, split: str) -> List[Item]:
        return [it for it in self.items if it.split == split]


def load_layout(root: Path, manifest: Dict[str, Any]) -> Layout:
    """The manifest's class order and items, checked against the files present."""
    root = Path(root)
    labels = [str(lbl) for lbl in manifest.get("labels") or []]
    if not 2 <= len(labels) <= MAX_CLASSES or len(set(labels)) != len(labels):
        raise DatasetError(f"labels must be 2 to {MAX_CLASSES} distinct classes, got {labels}")
    index = {lbl: i for i, lbl in enumerate(labels)}
    items: List[Item] = []
    for raw in manifest.get("items") or []:
        label, split, rel = raw.get("label"), raw.get("split"), raw.get("file", "")
        if label not in index or split not in SPLITS:
            raise DatasetError(f"item {raw} has an unknown label or split")
        path = root / rel
        if not path.is_file():
            raise DatasetError(f"item file {rel} is missing")
        items.append(Item(path=path, label_index=index[label], split=split))
    layout = Layout(labels=labels, items=items)
    empty = [lbl for lbl, n in layout.counts()["train"].items() if n == 0]
    if empty:
        raise DatasetError(f"no training images for: {', '.join(empty)}")
    if not layout.by_split("test"):
        raise DatasetError("no test images; the held-out split is needed for the metrics")
    return layout


# ── Numbers ───────────────────────────────────────────────────────────


def class_weights(counts: Sequence[int]) -> Dict[int, float]:
    """Balanced weights, ``total / (n_classes * count)``, so rare classes count as much as common ones."""
    total = float(sum(counts))
    n = len(counts)
    return {i: (total / (n * c) if c else 0.0) for i, c in enumerate(counts)}


def fit_short_box(width: int, height: int, size: int) -> Tuple[int, int, Tuple[int, int, int, int]]:
    """Resize the shortest side to ``size`` then centre-crop a ``size`` square.

    Returns ``(resized_width, resized_height, crop_box)``; the same geometry as the
    Edge Impulse recipe (``fit-short``, ``middle-center``) the rat model used.
    """
    if width <= 0 or height <= 0:
        raise DatasetError("image has no pixels")
    if width < height:
        rw, rh = size, max(size, round(height * size / width))
    else:
        rw, rh = max(size, round(width * size / height)), size
    left = (rw - size) // 2
    top = (rh - size) // 2
    return rw, rh, (left, top, left + size, top + size)


def representative_indices(n: int, k: int, seed: int) -> List[int]:
    """Deterministic sample of ``k`` indexes out of ``n`` for the quantisation calibration."""
    if n <= 0:
        return []
    k = min(k, n)
    state = seed
    picked: List[int] = []
    seen = set()
    while len(picked) < k:
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        idx = state % n
        if idx not in seen:
            seen.add(idx)
            picked.append(idx)
    return picked
