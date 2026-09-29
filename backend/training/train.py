#!/usr/bin/env python3
# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Train a Species Brain: MobileNetV2 (alpha 0.35) transfer learning → int8 TFLite.

The recipe the rat and person models were trained with by hand in Edge Impulse,
as code (parameters in ``dataset_utils.Config``: the manifest's recipe, then CLI flags):

    input           96×96 (or 160×160) grayscale (or RGB), fit-short resize + centre crop
    base            keras.applications.MobileNetV2(alpha=0.35, include_top=False, weights="imagenet"), frozen
    head            GlobalAveragePooling → Dropout(0.1) → Dense(16, relu) → Dense(classes, softmax)
    training        30 epochs, Adam 0.001, augmentation (flip, brightness, contrast, zoom), balanced class weights
    hold-out        the manifest's ``test`` items (the website's split); never used for a decision here
    quantisation    full-integer int8, representative dataset, int8 input and output

The container stops there: the website compiles with Vela, checks the tensor arena and
LM-1, and checks the int8 input contract below (``app/domain/trainer.py``).

Grayscale with ImageNet weights: the pretrained MobileNetV2 has a 3-channel stem and
Keras ships its weights only for 3-channel inputs, so the model takes a 1-channel
image and repeats it into three identical channels *inside the graph*
(``Concatenate`` of the same tensor). The camera therefore still feeds one channel,
which is all its ``img_rescale`` writes, and every pretrained weight loads unchanged.
The alternative, a trainable 1→3 conv stem, would start from random weights on the
first layer; channel replication is exact and costs one cheap op.

Input contract with the firmware: ``cvapp.cpp::img_rescale`` writes ``pixel - 128`` as
int8. The model's first layer rescales [0, 255] to [-1, 1] itself, so the quantised
input covers 0..255 with scale 1.0 and zero point -128, which is exactly ``pixel - 128``.
Two synthetic calibration samples (all 0 and all 255) pin that range.

Outputs (``TRAINING_OUTPUT_URI`` or ``--output``): ``model_int8.tflite``,
``model_float.keras``, ``labels.txt`` (class order, LF) and ``metrics.json``
(``accuracy`` = int8 held-out accuracy, plus float and int8 confusion matrices,
per-class scores, history, recipe, dataset counts, timings, versions).

Run locally on a folder with a manifest:  python train.py --input ./dataset --output ./out --epochs 5
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from dataset_utils import (
    Config,
    DatasetError,
    Layout,
    class_weights,
    config_from,
    fit_short_box,
    load_layout,
    read_manifest,
    representative_indices,
)
from gcs_io import publish_output, stage_input


def log(msg: str) -> None:
    print(f"[train] {msg}", flush=True)


# ── Images ────────────────────────────────────────────────────────────


def load_image(path: Path, size: int, channels: int) -> np.ndarray:
    """Decode, fit-short resize, centre crop → uint8 array (size, size, channels)."""
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("L" if channels == 1 else "RGB")
        rw, rh, box = fit_short_box(im.width, im.height, size)
        im = im.resize((rw, rh), Image.LANCZOS).crop(box)
        arr = np.asarray(im, dtype=np.uint8)
    if channels == 1:
        arr = arr[:, :, None]
    return arr


def load_split(layout: Layout, split: str, cfg: Config) -> Tuple[np.ndarray, np.ndarray]:
    items = layout.by_split(split)
    xs = np.zeros((len(items), cfg.image_size, cfg.image_size, cfg.channels), dtype=np.uint8)
    ys = np.zeros((len(items),), dtype=np.int32)
    bad = 0
    for i, it in enumerate(items):
        try:
            xs[i] = load_image(it.path, cfg.image_size, cfg.channels)
            ys[i] = it.label_index
        except Exception as exc:  # noqa: BLE001 one broken image must not sink the run
            bad += 1
            log(f"skipping unreadable image {it.path}: {exc}")
            ys[i] = -1
    keep = ys >= 0
    if bad:
        log(f"{bad} unreadable image(s) dropped from {split}")
    return xs[keep], ys[keep]


# ── Model ─────────────────────────────────────────────────────────────


def build_model(size: int, channels: int, n_classes: int, *, alpha: float, pretrained: bool):
    import tensorflow as tf

    keras = tf.keras
    inp = keras.Input(shape=(size, size, channels), name="image")
    x = keras.layers.Rescaling(1.0 / 127.5, offset=-1.0, name="to_unit_range")(inp)
    if channels == 1:
        # ImageNet weights need three channels; replicate the grayscale plane (see module doc).
        x = keras.layers.Concatenate(axis=-1, name="gray_to_rgb")([x, x, x])
    base = keras.applications.MobileNetV2(input_shape=(size, size, 3), alpha=alpha, include_top=False, weights="imagenet" if pretrained else None)
    base.trainable = False
    x = base(x, training=False)
    x = keras.layers.GlobalAveragePooling2D(name="pool")(x)
    x = keras.layers.Dropout(0.1, name="dropout")(x)
    x = keras.layers.Dense(16, activation="relu", name="head")(x)
    out = keras.layers.Dense(n_classes, activation="softmax", name="classes")(x)
    return keras.Model(inp, out, name="species_brain")


def make_datasets(x_train, y_train, x_test, y_test, cfg: Config):
    import tensorflow as tf

    size = cfg.image_size

    def to_float(img, label):
        return tf.cast(img, tf.float32), label

    def augment(img, label):
        img = tf.image.random_flip_left_right(img)
        img = tf.image.random_brightness(img, 25.0)
        img = tf.image.random_contrast(img, 0.8, 1.2)
        zoom = tf.random.uniform([], 0.85, 1.0)
        crop = tf.cast(tf.round(zoom * size), tf.int32)
        img = tf.image.random_crop(img, [crop, crop, cfg.channels])
        img = tf.image.resize(img, [size, size])
        return tf.clip_by_value(img, 0.0, 255.0), label

    train = tf.data.Dataset.from_tensor_slices((x_train, y_train)).shuffle(len(x_train), seed=cfg.seed, reshuffle_each_iteration=True).map(to_float)
    if cfg.augmentation:
        train = train.map(augment, num_parallel_calls=tf.data.AUTOTUNE)
    train = train.batch(cfg.batch_size).prefetch(tf.data.AUTOTUNE)
    test = tf.data.Dataset.from_tensor_slices((x_test, y_test)).map(to_float).batch(cfg.batch_size).prefetch(tf.data.AUTOTUNE)
    return train, test


def confusion(y_true: np.ndarray, y_pred: np.ndarray, n: int) -> List[List[int]]:
    m = np.zeros((n, n), dtype=np.int64)
    for t, p in zip(y_true, y_pred):
        m[t, p] += 1
    return m.tolist()


def per_class(matrix: List[List[int]], labels: List[str]) -> Dict[str, Dict[str, float]]:
    m = np.asarray(matrix, dtype=np.float64)
    out: Dict[str, Dict[str, float]] = {}
    for i, lbl in enumerate(labels):
        tp = m[i, i]
        fn = m[i, :].sum() - tp
        fp = m[:, i].sum() - tp
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        out[lbl] = {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4), "support": int(m[i, :].sum())}
    return out


def evaluate_float(model, x_test: np.ndarray, y_test: np.ndarray, labels: List[str]) -> Dict[str, Any]:
    probs = model.predict(x_test.astype(np.float32), verbose=0)
    pred = probs.argmax(axis=1)
    matrix = confusion(y_test, pred, len(labels))
    return {
        "accuracy": round(float((pred == y_test).mean()), 4),
        "confusion_matrix": matrix,
        "per_class": per_class(matrix, labels),
        "n": int(len(y_test)),
    }


# ── Quantisation ──────────────────────────────────────────────────────


def convert_int8(model, rep_images: np.ndarray, size: int, channels: int) -> bytes:
    import tensorflow as tf

    def representative():
        # Pin the calibration range to the full 0..255 so the input quantises to
        # scale 1.0 / zero point -128, which is what the firmware feeds (pixel - 128).
        yield [np.zeros((1, size, size, channels), dtype=np.float32)]
        yield [np.full((1, size, size, channels), 255.0, dtype=np.float32)]
        for img in rep_images:
            yield [img[None].astype(np.float32)]

    try:
        converter = tf.lite.TFLiteConverter.from_keras_model(model)
    except Exception as exc:  # noqa: BLE001 Keras 3 models sometimes need the SavedModel route
        log(f"from_keras_model failed ({exc}); exporting a SavedModel instead")
        export_dir = tempfile.mkdtemp(prefix="ww-export-")
        model.export(export_dir)
        converter = tf.lite.TFLiteConverter.from_saved_model(export_dir)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset = representative
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type = tf.int8
    converter.inference_output_type = tf.int8
    return converter.convert()


def evaluate_int8(tflite_bytes: bytes, x_test: np.ndarray, y_test: np.ndarray, labels: List[str]) -> Dict[str, Any]:
    import tensorflow as tf

    interp = tf.lite.Interpreter(model_content=tflite_bytes)
    interp.allocate_tensors()
    inp, out = interp.get_input_details()[0], interp.get_output_details()[0]
    scale, zp = inp["quantization"]
    pred = np.zeros((len(x_test),), dtype=np.int64)
    for i, img in enumerate(x_test):
        q = np.clip(np.round(img.astype(np.float32) / scale + zp), -128, 127).astype(np.int8)
        interp.set_tensor(inp["index"], q[None])
        interp.invoke()
        pred[i] = int(np.argmax(interp.get_tensor(out["index"])[0]))
    matrix = confusion(y_test, pred, len(labels))
    return {
        "accuracy": round(float((pred == y_test).mean()), 4),
        "confusion_matrix": matrix,
        "per_class": per_class(matrix, labels),
        "n": int(len(y_test)),
    }


# ── The run ───────────────────────────────────────────────────────────


def versions() -> Dict[str, str]:
    info = {"python": platform.python_version(), "numpy": np.__version__}
    try:
        import tensorflow as tf

        info["tensorflow"] = tf.__version__
        info["keras"] = getattr(tf.keras, "__version__", "")
        info["gpus"] = str(len(tf.config.list_physical_devices("GPU")))
    except Exception:  # noqa: BLE001 informational
        pass
    return info


def run(env: Dict[str, str], overrides: Optional[Dict[str, Any]] = None, *, scratch: Optional[Path] = None) -> Dict[str, Any]:
    import tensorflow as tf

    started = time.time()
    overrides = overrides or {}
    scratch = Path(scratch or tempfile.mkdtemp(prefix="ww-train-"))
    input_dir = stage_input(overrides.get("input_uri") or env.get("TRAINING_INPUT_URI", "/data/input"), scratch)
    manifest = read_manifest(input_dir)
    cfg: Config = config_from(env, manifest, overrides)
    tf.keras.utils.set_random_seed(cfg.seed)
    out_dir = Path(cfg.output_uri) if not cfg.output_uri.startswith("gs://") else scratch / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    layout = load_layout(input_dir, manifest)
    labels = layout.labels
    counts = layout.counts()
    log(f"classes (device order): {labels}")
    log(f"images: train {sum(counts['train'].values())}, test {sum(counts['test'].values())}; per class {counts}")

    t0 = time.time()
    x_train, y_train = load_split(layout, "train", cfg)
    x_test, y_test = load_split(layout, "test", cfg)
    if len(x_train) == 0 or len(x_test) == 0:
        raise DatasetError("no readable images in train or test")
    load_s = time.time() - t0

    model = build_model(cfg.image_size, cfg.channels, len(labels), alpha=cfg.alpha, pretrained=cfg.pretrained)
    model.compile(optimizer=tf.keras.optimizers.Adam(cfg.learning_rate), loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    train_ds, test_ds = make_datasets(x_train, y_train, x_test, y_test, cfg)
    weights = class_weights([int((y_train == i).sum()) for i in range(len(labels))]) if cfg.class_weights else None

    t0 = time.time()
    history = model.fit(train_ds, epochs=cfg.epochs, validation_data=test_ds, class_weight=weights, verbose=2)
    train_s = time.time() - t0
    float_metrics = evaluate_float(model, x_test, y_test, labels)
    log(f"float accuracy {float_metrics['accuracy']} on {float_metrics['n']} held-out images ({train_s:.0f}s training)")

    t0 = time.time()
    rep = x_train[representative_indices(len(x_train), cfg.representative_samples, cfg.seed)]
    tflite_bytes = convert_int8(model, rep, cfg.image_size, cfg.channels)
    int8_metrics = evaluate_int8(tflite_bytes, x_test, y_test, labels)
    quant_s = time.time() - t0
    log(f"int8 accuracy {int8_metrics['accuracy']}")

    (out_dir / "model_int8.tflite").write_bytes(tflite_bytes)
    (out_dir / "labels.txt").write_text("\n".join(labels), encoding="utf-8", newline="\n")
    model.save(out_dir / "model_float.keras")

    metrics = {
        "accuracy": int8_metrics["accuracy"],
        "run_key": cfg.run_key,
        "model_name": cfg.model_name,
        "labels": labels,
        "dataset": {"counts": counts, "train": int(len(x_train)), "test": int(len(x_test))},
        "recipe": cfg.recipe_dict(),
        "float": float_metrics,
        "int8": int8_metrics,
        "history": {k: [round(float(v), 5) for v in vals] for k, vals in history.history.items()},
        "int8_bytes": len(tflite_bytes),
        "timings": {
            "load_s": round(load_s, 1),
            "train_s": round(train_s, 1),
            "quantise_s": round(quant_s, 1),
            "total_s": round(time.time() - started, 1),
        },
        "versions": versions(),
    }
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8", newline="\n")
    publish_output(out_dir, cfg.output_uri)
    log(f"done in {metrics['timings']['total_s']}s; outputs in {cfg.output_uri}")
    return metrics


def parse_args(argv=None) -> Dict[str, Any]:
    p = argparse.ArgumentParser(description="Train a Species Brain (MobileNetV2 0.35 → int8 TFLite)")
    p.add_argument("--input", dest="input_uri", help="folder or gs:// prefix with manifest.json and the images")
    p.add_argument("--output", dest="output_uri", help="folder or gs:// prefix for the artefacts")
    p.add_argument("--image-size", dest="image_size", type=int, choices=(96, 160))
    p.add_argument("--colour", choices=("grayscale", "rgb"))
    p.add_argument("--epochs", type=int)
    p.add_argument("--learning-rate", dest="learning_rate", type=float)
    p.add_argument("--batch-size", dest="batch_size", type=int)
    p.add_argument("--no-augmentation", dest="augmentation", action="store_false", default=None)
    p.add_argument("--no-class-weights", dest="class_weights", action="store_false", default=None)
    p.add_argument("--no-pretrained", dest="pretrained", action="store_false", default=None, help="random init (tests, offline)")
    return dict(vars(p.parse_args(argv)))


def main(argv=None) -> int:
    try:
        run(dict(os.environ), parse_args(argv))
    except (DatasetError, RuntimeError) as exc:
        log(f"error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "1")
    sys.exit(main())
