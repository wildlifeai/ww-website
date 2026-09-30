# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native (Google Cloud) Species Brain training: the dataset handed to the container
and the checks on what comes back. Used by ``jobs/native_training.py``.

The dataset and its split are #151's (``domain/training.py::build_training_dataset``);
this module only writes them as the container's manifest. The container stops at
the int8 TFLite; Vela, the arena check (``services/vela.py``) and LM-1
(``domain/model.py::convert_uploaded_model``) run here on the worker, as for an
uploaded model. What only this path checks is the int8 input contract, below.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.domain.model import read_io_tensors
from app.services.gcp_training import ARTIFACT_LABELS, ARTIFACT_METRICS, ARTIFACT_MODEL_INT8, MANIFEST_NAME, REQUIRED_ARTIFACTS
from app.services.vela import run_vela_conversion

MANIFEST_SCHEMA_VERSION = 1

# cvapp.cpp img_rescale() writes ``pixel - 128`` as int8 into one channel. The model reads
# ``x = scale * (q - zero_point)``, so x is the pixel value it was trained on only for these
# (name, scale, zero points); anything else feeds the model numbers it never saw.
FIRMWARE_INPUT_CONTRACTS = (
    ("pixels 0..255", 1.0, (-128,)),  # this container: Rescaling inside the graph
    ("pixels 0..1", 1.0 / 255, (-128,)),  # Edge Impulse image block
    ("pixels -1..1", 2.0 / 255, (-1, 0)),  # preprocess_input outside the graph
)
FIRMWARE_INPUT_SCALE_TOLERANCE = 0.02  # relative


class ArtifactValidationError(Exception):
    """The trained model would run wrongly on the camera; the message says why."""


def make_run_key(model_id: str) -> str:
    """GCS-safe run key from the ``ai_models`` id, so a retried job lands on the same prefix."""
    key = re.sub(r"[^a-z0-9-]+", "-", str(model_id).strip().lower()).strip("-")
    if not key:
        raise ValueError("model_id is required to build a run key")
    return key


def build_dataset_objects(
    train: Sequence[Tuple[Any, bytes]],
    test: Sequence[Tuple[Any, bytes]],
    labels: Sequence[str],
    *,
    run_key: str,
    model_name: str,
    recipe: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[Tuple[str, bytes]]]:
    """#151's ``(TrainingSample, bytes)`` split → ``(manifest, [(path, bytes), …])``.

    Paths are relative to the run's dataset prefix; the manifest is the last object.
    Each item carries its split, so the container never re-splits.
    """
    items: List[Dict[str, str]] = []
    objects: List[Tuple[str, bytes]] = []
    for split, rows in (("train", train), ("test", test)):
        for sample, data in rows:
            path = f"images/{len(items):05d}.jpg"
            items.append({"file": path, "label": sample.label, "split": split})
            objects.append((path, data))
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "run_key": run_key,
        "model_name": model_name,
        "labels": list(labels),
        "recipe": dict(recipe),
        "items": items,
    }
    objects.append((MANIFEST_NAME, json.dumps(manifest, indent=2).encode("utf-8")))
    return manifest, objects


@dataclass
class TrainingArtifacts:
    """The container's output directory."""

    directory: Path

    @classmethod
    def from_directory(cls, directory: Path) -> "TrainingArtifacts":
        directory = Path(directory)
        missing = [name for name in REQUIRED_ARTIFACTS if not (directory / name).is_file()]
        if missing:
            raise ArtifactValidationError(f"training output is missing {', '.join(missing)}")
        return cls(directory)

    @property
    def model_int8(self) -> Path:
        return self.directory / ARTIFACT_MODEL_INT8

    def read_labels(self) -> List[str]:
        text = (self.directory / ARTIFACT_LABELS).read_text(encoding="utf-8")
        return [line.strip() for line in text.splitlines() if line.strip()]

    def metrics(self) -> Dict[str, Any]:
        try:
            data = json.loads((self.directory / ARTIFACT_METRICS).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ArtifactValidationError(f"metrics.json is unreadable: {exc}") from exc
        if not isinstance(data, dict):
            raise ArtifactValidationError("metrics.json is not a JSON object")
        return data


def input_contract(scale: Optional[float], zero_point: Optional[int]) -> Optional[str]:
    """Which pixel mapping an int8 input's (scale, zero_point) implements, or None if the camera cannot feed it."""
    if scale is None or zero_point is None or scale <= 0:
        return None
    for name, expected, zero_points in FIRMWARE_INPUT_CONTRACTS:
        if zero_point in zero_points and abs(scale - expected) / expected <= FIRMWARE_INPUT_SCALE_TOLERANCE:
            return name
    return None


def validate_artifacts(artifacts: TrainingArtifacts, *, expected_labels: Sequence[str], image_size: int, colour: str) -> Dict[str, Any]:
    """The checks nothing else performs: label order, int8 in/out, the firmware input contract.

    LM-1 runs in ``convert_uploaded_model`` and the arena check in ``run_vela_conversion``.
    Returns a summary for ``processing_log`` (``warnings`` lists what did not fail).
    """
    labels = artifacts.read_labels()
    if labels != list(expected_labels):
        raise ArtifactValidationError(f"labels.txt order {labels} is not the dataset class order {list(expected_labels)}")
    try:
        inp, out = read_io_tensors(artifacts.model_int8)
    except ValueError as exc:
        raise ArtifactValidationError(str(exc)) from exc
    if inp is None or inp.dtype != "INT8" or out.dtype != "INT8":
        raise ArtifactValidationError(
            f"the model needs int8 input and output tensors, got {inp.dtype if inp else 'no input'} and {out.dtype}; "
            "re-run the full-integer quantisation"
        )
    if len(inp.shape) != 4 or tuple(inp.shape[:3]) != (1, image_size, image_size):
        raise ArtifactValidationError(f"input tensor {list(inp.shape)} is not [1, {image_size}, {image_size}, C]")
    warnings: List[str] = []
    if inp.shape[3] != 1:
        if colour != "rgb":
            raise ArtifactValidationError(f"grayscale recipe but the input tensor has {inp.shape[3]} channels")
        warnings.append("RGB input: the ww500_md firmware fills one channel (img_rescale), so the camera will not feed it a real image")
    contract = input_contract(inp.scale, inp.zero_point)
    if contract is None:
        raise ArtifactValidationError(
            f"input quantisation scale={inp.scale} zero_point={inp.zero_point} matches no pixel mapping the firmware's "
            "pixel - 128 feed can satisfy; the representative dataset must span the full pixel range"
        )
    return {
        "labels": labels,
        "input_shape": list(inp.shape),
        "output_shape": list(out.shape),
        "input_scale": inp.scale,
        "input_zero_point": inp.zero_point,
        "input_contract": contract,
        "warnings": warnings,
    }


def package_precompiled_zip(tfl_bytes: bytes, labels: Sequence[str]) -> bytes:
    """``model.tfl`` + ``labels.txt``: the package ``convert_uploaded_model`` takes as precompiled."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("model.tfl", tfl_bytes)
        z.writestr("labels.txt", "\n".join(labels))
    return buf.getvalue()


async def compile_for_camera(artifacts: TrainingArtifacts, *, expected_labels: Sequence[str], image_size: int, colour: str) -> Tuple[bytes, Dict]:
    """Validate, compile with Vela (arena check included) and package → ``(zip_bytes, check)``."""
    check = validate_artifacts(artifacts, expected_labels=expected_labels, image_size=image_size, colour=colour)
    vela_dir = artifacts.directory / "vela"
    vela_dir.mkdir(exist_ok=True)
    tfl_path = await run_vela_conversion(artifacts.model_int8, vela_dir)
    return package_precompiled_zip(tfl_path.read_bytes(), check["labels"]), check
