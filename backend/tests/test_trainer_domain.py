# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""domain/trainer.py: the manifest built from #151's split, the int8 checks, the packaging."""

import io
import json
import zipfile

import pytest

from app.domain import trainer
from app.domain.model import ModelDomainError, convert_uploaded_model, read_io_tensors
from app.domain.trainer import (
    ArtifactValidationError,
    TrainingArtifacts,
    build_dataset_objects,
    compile_for_camera,
    input_contract,
    make_run_key,
    package_precompiled_zip,
    validate_artifacts,
)
from app.domain.training import BACKGROUND_ROLE, TARGET_ROLE, TrainingSample, split_samples
from tests.tflite_fixtures import build_classifier_tflite, write_artifact_dir

LABELS = ["not rat", "rat"]


def _sample(i, label):
    role = BACKGROUND_ROLE if label == "not rat" else TARGET_ROLE
    return TrainingSample(f"obs-{i}", f"media-{i}", "dep-1", label, role, f"crop-{i}.jpg", False)


class TestManifest:
    def test_items_carry_151s_split(self):
        samples = [_sample(i, LABELS[i % 2]) for i in range(40)]
        train, test = split_samples(samples)
        rows_train = [(s, s.sample_id.encode()) for s in train]
        rows_test = [(s, s.sample_id.encode()) for s in test]
        manifest, objects = build_dataset_objects(rows_train, rows_test, LABELS, run_key="k", model_name="Rat", recipe={"image_size": 96})
        assert manifest["labels"] == LABELS and manifest["schema_version"] == 1 and manifest["recipe"] == {"image_size": 96}
        by_split = {s: [it for it in manifest["items"] if it["split"] == s] for s in ("train", "test")}
        assert len(by_split["train"]) == len(train) and len(by_split["test"]) == len(test)
        assert [it["label"] for it in by_split["test"]] == [s.label for s in test]
        # every item has its bytes under the same path; the manifest is the last object
        paths = dict(objects)
        assert objects[-1][0] == "manifest.json" and json.loads(objects[-1][1]) == manifest
        assert all(paths[it["file"]] for it in manifest["items"]) and len(set(paths)) == len(objects)

    def test_run_key(self):
        assert make_run_key("A3C7-c373_x") == "a3c7-c373-x"
        with pytest.raises(ValueError):
            make_run_key("---")


class TestReadIoTensors:
    def test_reads_dtype_shape_and_quantisation(self, tmp_path):
        p = tmp_path / "m.tflite"
        p.write_bytes(build_classifier_tflite(input_shape=[1, 96, 96, 1], output_classes=2))
        inp, out = read_io_tensors(p)
        assert (inp.dtype, inp.shape, inp.scale, inp.zero_point) == ("INT8", (1, 96, 96, 1), 1.0, -128)
        assert (out.dtype, out.shape) == ("INT8", (1, 2))

    def test_garbage_is_a_value_error(self, tmp_path):
        p = tmp_path / "x.tflite"
        p.write_bytes(b"not a model")
        with pytest.raises(ValueError):
            read_io_tensors(p)


class TestValidateArtifacts:
    def _art(self, tmp_path, **kw):
        return TrainingArtifacts.from_directory(write_artifact_dir(tmp_path / "a", labels=LABELS, **kw))

    def _check(self, art, colour="grayscale", size=96, labels=LABELS):
        return validate_artifacts(art, expected_labels=labels, image_size=size, colour=colour)

    def test_good_model_passes(self, tmp_path):
        check = self._check(self._art(tmp_path))
        assert check["input_contract"] == "pixels 0..255" and check["input_shape"] == [1, 96, 96, 1] and check["warnings"] == []

    def test_missing_output_file(self, tmp_path):
        d = write_artifact_dir(tmp_path / "a", labels=LABELS)
        (d / "metrics.json").unlink()
        with pytest.raises(ArtifactValidationError, match="missing metrics.json"):
            TrainingArtifacts.from_directory(d)

    def test_label_order_must_be_the_dataset_order(self, tmp_path):
        with pytest.raises(ArtifactValidationError, match="class order"):
            self._check(self._art(tmp_path), labels=["rat", "not rat"])

    def test_float_model_is_refused(self, tmp_path):
        with pytest.raises(ArtifactValidationError, match="int8 input and output"):
            self._check(self._art(tmp_path, dtype="FLOAT32", input_quant=None))

    def test_input_size_must_match_the_recipe(self, tmp_path):
        with pytest.raises(ArtifactValidationError, match=r"not \[1, 96, 96, C\]"):
            self._check(self._art(tmp_path, image_size=160))

    def test_channels(self, tmp_path):
        art = self._art(tmp_path, channels=3)
        with pytest.raises(ArtifactValidationError, match="3 channels"):
            self._check(art)
        assert "img_rescale" in self._check(art, colour="rgb")["warnings"][0]

    def test_firmware_input_contract(self, tmp_path):
        with pytest.raises(ArtifactValidationError, match="pixel - 128"):
            self._check(self._art(tmp_path, input_quant=(0.0078, -128)))

    def test_input_contract_table(self):
        assert input_contract(1.0, -128) == "pixels 0..255"
        assert input_contract(0.00392, -128) == "pixels 0..1"
        assert input_contract(0.00784, -1) == input_contract(0.00784, 0) == "pixels -1..1"
        assert input_contract(0.00784, -128) is None and input_contract(1.0, 0) is None and input_contract(None, -128) is None


class TestPackaging:
    async def test_precompiled_zip_takes_the_upload_branch_with_lm1(self, tmp_path):
        tfl = build_classifier_tflite(input_shape=[1, 96, 96, 1], output_classes=2)
        z = zipfile.ZipFile(io.BytesIO(package_precompiled_zip(tfl, LABELS)))
        assert sorted(z.namelist()) == ["labels.txt", "model.tfl"] and z.read("labels.txt") == b"not rat\nrat"
        out_tfl, txt, labels = await convert_uploaded_model(package_precompiled_zip(tfl, LABELS), "rat-custom-v1.zip")
        assert (out_tfl, txt, labels) == (tfl, b"not rat\nrat", LABELS)
        three = build_classifier_tflite(input_shape=[1, 96, 96, 1], output_classes=3)
        with pytest.raises(ModelDomainError, match="Label/tensor mismatch"):
            await convert_uploaded_model(package_precompiled_zip(three, LABELS), "rat-custom-v1.zip")

    async def test_compile_for_camera_runs_vela_on_the_int8_model(self, tmp_path, monkeypatch):
        art = TrainingArtifacts.from_directory(write_artifact_dir(tmp_path / "a", labels=LABELS))
        seen = []

        async def fake_vela(src, out_dir):
            seen.append(src)
            (out_dir / "model_int8_vela.tflite").write_bytes(b"compiled")
            return out_dir / "model_int8_vela.tflite"

        monkeypatch.setattr(trainer, "run_vela_conversion", fake_vela)
        zip_bytes, check = await compile_for_camera(art, expected_labels=LABELS, image_size=96, colour="grayscale")
        assert seen == [art.model_int8] and check["labels"] == LABELS
        assert zipfile.ZipFile(io.BytesIO(zip_bytes)).read("model.tfl") == b"compiled"

    async def test_compile_for_camera_checks_before_vela(self, tmp_path, monkeypatch):
        art = TrainingArtifacts.from_directory(write_artifact_dir(tmp_path / "a", labels=LABELS, dtype="FLOAT32", input_quant=None))

        async def never(*a, **k):
            raise AssertionError("Vela must not run on a model that fails the checks")

        monkeypatch.setattr(trainer, "run_vela_conversion", never)
        with pytest.raises(ArtifactValidationError):
            await compile_for_camera(art, expected_labels=LABELS, image_size=96, colour="grayscale")
