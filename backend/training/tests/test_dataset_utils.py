# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure trainer helpers: config from the manifest, layout from the manifest's items, weights, crop geometry."""

import json

import pytest
from dataset_utils import DatasetError, class_weights, config_from, fit_short_box, load_layout, read_manifest, representative_indices


def _manifest(items, labels=("zz background", "rat"), **extra):
    return {"schema_version": 1, "run_key": "k", "model_name": "Rat", "labels": list(labels), "items": items, **extra}


def _write(root, manifest):
    root.mkdir(parents=True, exist_ok=True)
    for it in manifest["items"]:
        (root / it["file"]).parent.mkdir(parents=True, exist_ok=True)
        (root / it["file"]).write_bytes(b"\xff\xd8fake")
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root


def _item(i, label, split):
    return {"file": f"images/{i:05d}.jpg", "label": label, "split": split}


class TestConfig:
    def test_recipe_comes_from_the_manifest(self):
        cfg = config_from({"TRAINING_INPUT_URI": "gs://b/x"}, _manifest([], recipe={"image_size": 160, "colour": "rgb", "epochs": 12}))
        assert (cfg.image_size, cfg.colour, cfg.epochs, cfg.learning_rate, cfg.alpha) == (160, "rgb", 12, 0.001, 0.35)
        assert cfg.channels == 3 and cfg.input_uri == "gs://b/x" and cfg.run_key == "k"

    def test_overrides_and_validation(self):
        assert config_from({}, _manifest([]), {"epochs": 7, "output_uri": None}).epochs == 7
        with pytest.raises(DatasetError):
            config_from({}, _manifest([], recipe={"image_size": 224}))


class TestLayout:
    def test_split_and_class_order_come_from_the_manifest(self, tmp_path):
        # "rat" sorts before "zz background": only the manifest keeps background at index 0
        items = [_item(0, "zz background", "train"), _item(1, "rat", "train"), _item(2, "rat", "test"), _item(3, "zz background", "test")]
        root = _write(tmp_path, _manifest(items))
        layout = load_layout(root, read_manifest(root))
        assert layout.labels == ["zz background", "rat"]
        assert layout.counts() == {"train": {"zz background": 1, "rat": 1}, "test": {"zz background": 1, "rat": 1}}
        assert [it.split for it in layout.items] == ["train", "train", "test", "test"]

    def test_refusals(self, tmp_path):
        with pytest.raises(DatasetError, match="does not exist"):
            read_manifest(tmp_path)
        (tmp_path / "manifest.json").write_text(json.dumps({"schema_version": 2}))
        with pytest.raises(DatasetError, match="schema_version 1"):
            read_manifest(tmp_path)
        root = _write(tmp_path / "a", _manifest([_item(0, "rat", "train"), _item(1, "rat", "test")]))
        with pytest.raises(DatasetError, match="no training images for: zz background"):
            load_layout(root, read_manifest(root))
        with pytest.raises(DatasetError, match="2 to 16"):
            load_layout(root, _manifest([], labels=["rat"]))
        with pytest.raises(DatasetError, match="unknown label or split"):
            load_layout(root, _manifest([_item(0, "stoat", "train")]))
        with pytest.raises(DatasetError, match="missing"):
            load_layout(root, _manifest([_item(9, "rat", "train")]))


class TestNumbers:
    def test_class_weights_balance(self):
        w = class_weights([300, 100])
        assert w[0] == pytest.approx(400 / (2 * 300)) and w[1] == pytest.approx(400 / (2 * 100))
        assert class_weights([0, 5])[0] == 0.0

    def test_fit_short_box(self):
        assert fit_short_box(200, 100, 96) == (192, 96, (48, 0, 144, 96))
        assert fit_short_box(100, 300, 96) == (96, 288, (0, 96, 96, 192))
        assert fit_short_box(50, 50, 96) == (96, 96, (0, 0, 96, 96))
        with pytest.raises(DatasetError):
            fit_short_box(0, 10, 96)

    def test_representative_indices(self):
        idx = representative_indices(50, 10, 42)
        assert len(idx) == 10 and len(set(idx)) == 10 and all(0 <= i < 50 for i in idx)
        assert idx == representative_indices(50, 10, 42)
        assert len(representative_indices(3, 10, 1)) == 3 and representative_indices(0, 5, 1) == []
