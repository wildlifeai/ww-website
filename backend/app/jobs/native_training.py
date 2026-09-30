# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Google Cloud trainer half of ``train_species_brain_job`` (``training_mode() == "gcp"``).

#151's job builds the dataset, sets the row to ``uploaded`` and afterwards registers
whatever ZIP this returns through ``convert_uploaded_model`` (LM-1), ``build_label_map``
and ``store_model_artifacts``, exactly as for the Edge Impulse export. This function
only trains (Cloud Run), checks the int8 model and compiles it with Vela.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Sequence, Tuple

from app.config import settings
from app.domain.trainer import TrainingArtifacts, build_dataset_objects, compile_for_camera, make_run_key
from app.services.gcp_training import GcpCloudRunTrainer

ProgressFn = Callable[[float, str], Awaitable[None]]
TickFn = Callable[[str], Awaitable[None]]


async def train_on_gcp(
    model_id: str,
    req: Any,
    train: Sequence[Tuple[Any, bytes]],
    test: Sequence[Tuple[Any, bytes]],
    labels: List[str],
    *,
    progress: ProgressFn,
    tick: TickFn,
) -> Tuple[bytes, Dict[str, Any]]:
    """Dataset → Cloud Run → checked, Vela-compiled model → ``(precompiled_zip, training_fields)``.

    ``req`` is #151's ``TrainModelRequest``; ``labels`` is ``summary.labels`` (device order).
    ``training_fields`` is merged into the ``processing_log`` training entry.
    """
    run_key = make_run_key(model_id)
    recipe = {"image_size": req.image_size, "colour": req.colour, "epochs": req.epochs, "learning_rate": req.learning_rate}
    _, objects = build_dataset_objects(train, test, labels, run_key=run_key, model_name=req.model_name, recipe=recipe)
    trainer = GcpCloudRunTrainer.from_settings(settings)

    with tempfile.TemporaryDirectory(prefix="ww-train-") as tmp:
        await progress(0.2, f"Training on Google Cloud ({len(train) + len(test)} images, {len(labels)} classes)…")
        out_dir = await trainer.run(
            run_key,
            objects,
            Path(tmp) / "output",
            poll_interval_s=settings.TRAINING_POLL_INTERVAL_S,
            timeout_s=settings.TRAINING_RUN_TIMEOUT_S,
            on_tick=tick,
        )
        artifacts = TrainingArtifacts.from_directory(out_dir)
        await progress(0.8, "Checking the model and compiling it for the camera (Vela)…")
        zip_bytes, check = await compile_for_camera(artifacts, expected_labels=labels, image_size=req.image_size, colour=req.colour)
        metrics = artifacts.metrics()

    return zip_bytes, {"trainer": "gcp", "run_key": run_key, "recipe": recipe, "metrics": metrics, "artifact_check": check}
