# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""End-to-end smoke test of train.py on synthetic images. Skipped without TensorFlow."""

import json

import numpy as np
import pytest

tf = pytest.importorskip("tensorflow")
PIL = pytest.importorskip("PIL")

from train import run  # noqa: E402


def _make_dataset(root, size=64):
    from PIL import Image

    rng = np.random.default_rng(0)
    (root / "images").mkdir(parents=True)
    items = []
    for split, n in (("train", 6), ("test", 2)):
        for label, base in (("not rat", 40), ("rat", 200)):
            for _ in range(n):
                name = f"images/{len(items):05d}.jpg"
                arr = np.clip(rng.normal(base, 20, (size + 10, size, 3)), 0, 255).astype(np.uint8)
                Image.fromarray(arr).save(root / name)
                items.append({"file": name, "label": label, "split": split})
    manifest = {"schema_version": 1, "run_key": "t", "model_name": "Rat", "labels": ["not rat", "rat"], "recipe": {"epochs": 1}, "items": items}
    (root / "manifest.json").write_text(json.dumps(manifest))


def test_train_and_quantise(tmp_path):
    _make_dataset(tmp_path / "in")
    overrides = {
        "input_uri": str(tmp_path / "in"),
        "output_uri": str(tmp_path / "out"),
        "batch_size": 4,
        "pretrained": False,  # no ImageNet download in tests
        "representative_samples": 4,
    }
    metrics = run({}, overrides, scratch=tmp_path / "scratch")
    out = tmp_path / "out"
    assert (out / "model_int8.tflite").is_file() and (out / "labels.txt").read_text() == "not rat\nrat"
    assert 0.0 <= metrics["accuracy"] <= 1.0 and len(metrics["history"]["loss"]) == 1
    interp = tf.lite.Interpreter(model_path=str(out / "model_int8.tflite"))
    inp = interp.get_input_details()[0]
    assert list(inp["shape"]) == [1, 96, 96, 1] and inp["dtype"] == np.int8 and inp["quantization"][1] == -128
