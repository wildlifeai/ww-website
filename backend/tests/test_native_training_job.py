# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The gcp branch of #151's train_species_brain_job and its trainer half (jobs/native_training.py)."""

import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain import trainer as trainer_domain
from app.domain.training import TrainingSample, training_mode
from app.jobs import native_training as nt
from tests.tflite_fixtures import write_artifact_dir

LABELS = ["not rat", "rat"]
REQ = SimpleNamespace(model_name="Rat brain", image_size=96, colour="grayscale", epochs=30, learning_rate=0.001)


def _rows(n, label):
    return [(TrainingSample(f"{label}-{i}", f"m-{i}", "d", label, "target", "c.jpg", False), b"jpg") for i in range(n)]


class FakeTrainer:
    def __init__(self, out_dir):
        self.out_dir, self.calls = out_dir, []

    async def run(self, run_key, objects, dest, **kw):
        self.calls.append((run_key, objects, kw))
        return self.out_dir


async def _noop(*a, **k):
    return None


@pytest.fixture
def fake_vela(monkeypatch):
    async def vela(src, out_dir):
        (out_dir / "model_int8_vela.tflite").write_bytes(b"compiled")
        return out_dir / "model_int8_vela.tflite"

    monkeypatch.setattr(trainer_domain, "run_vela_conversion", vela)


class TestTrainOnGcp:
    async def test_trains_checks_compiles_and_packages(self, tmp_path, monkeypatch, fake_vela):
        fake = FakeTrainer(write_artifact_dir(tmp_path / "out", labels=LABELS, accuracy=0.93))
        monkeypatch.setattr(nt.GcpCloudRunTrainer, "from_settings", classmethod(lambda cls, s: fake))
        zip_bytes, fields = await nt.train_on_gcp("A3C7-C373", REQ, _rows(3, "rat"), _rows(1, "not rat"), LABELS, progress=_noop, tick=_noop)
        run_key, objects, kw = fake.calls[0]
        manifest = json.loads(dict(objects)["manifest.json"])
        assert run_key == "a3c7-c373" and manifest["labels"] == LABELS and [i["split"] for i in manifest["items"]] == ["train"] * 3 + ["test"]
        assert manifest["recipe"] == {"image_size": 96, "colour": "grayscale", "epochs": 30, "learning_rate": 0.001}
        assert kw["timeout_s"] == nt.settings.TRAINING_RUN_TIMEOUT_S
        z = zipfile.ZipFile(io.BytesIO(zip_bytes))
        assert z.read("model.tfl") == b"compiled" and z.read("labels.txt") == b"not rat\nrat"
        assert fields["trainer"] == "gcp" and fields["metrics"]["accuracy"] == 0.93 and fields["artifact_check"]["input_contract"] == "pixels 0..255"

    async def test_wrong_label_order_never_reaches_vela(self, tmp_path, monkeypatch):
        fake = FakeTrainer(write_artifact_dir(tmp_path / "out", labels=["rat", "not rat"]))
        monkeypatch.setattr(nt.GcpCloudRunTrainer, "from_settings", classmethod(lambda cls, s: fake))
        monkeypatch.setattr(trainer_domain, "run_vela_conversion", AsyncMock(side_effect=AssertionError("Vela ran")))
        with pytest.raises(trainer_domain.ArtifactValidationError, match="class order"):
            await nt.train_on_gcp("m1", REQ, _rows(2, "rat"), [], LABELS, progress=_noop, tick=_noop)


class TestTrainingMode:
    def test_gcp_needs_both_the_flag_and_the_trainer(self, monkeypatch):
        from app.domain import training

        monkeypatch.setattr(training.settings, "EDGE_IMPULSE_API_KEY", "")
        monkeypatch.setattr(training.settings, "MODEL_TRAINER", "gcp")
        monkeypatch.setattr(training.settings, "FF_NATIVE_TRAINING_ENABLED", False)
        assert training_mode() == "export_only"
        monkeypatch.setattr(training.settings, "FF_NATIVE_TRAINING_ENABLED", True)
        assert training_mode() == "gcp"
        monkeypatch.setattr(training.settings, "MODEL_TRAINER", "edge_impulse")
        assert training_mode() == "export_only"


class TestJobWiring:
    async def test_gcp_branch_registers_through_151s_tail(self, monkeypatch):
        """Same row lifecycle as Edge Impulse: uploaded → convert_uploaded_model → store → validated."""
        from app.domain import training
        from app.jobs import definitions

        monkeypatch.setattr(training.settings, "MODEL_TRAINER", "gcp")
        monkeypatch.setattr(training.settings, "FF_NATIVE_TRAINING_ENABLED", True)
        summary = training.DatasetSummary(labels=LABELS, counts={"not rat": 20, "rat": 20})
        statuses, updates = [], []

        async def model_status(client, model_id, job_id, status, error_message=None, training=None, **fields):
            statuses.append((status, training, fields))

        async def update_job(job_id, **kw):
            updates.append(kw)

        client = MagicMock()
        row = {"version": "2.0.0-abc123", "ai_model_families": {"firmware_model_id": 42}}
        client.table.return_value.select.return_value.eq.return_value.execute.return_value = SimpleNamespace(data=[row])
        trained = {"trainer": "gcp", "run_key": "m1", "recipe": {}, "metrics": {"accuracy": 0.9}, "artifact_check": {}}
        params = {
            "media_ids": ["a", "b"],
            "model_name": "Rat brain",
            "classes": [{"label": "rat", "scientific_name": "Rattus rattus"}],
        }
        with (
            patch("app.services.supabase_client.create_service_client", lambda: client),
            patch("app.domain.training.build_training_dataset", AsyncMock(return_value=([], [], summary))),
            patch("app.jobs.native_training.train_on_gcp", AsyncMock(return_value=(b"zip", trained))) as gcp,
            patch("app.domain.model.convert_uploaded_model", AsyncMock(return_value=(b"tfl", b"not rat\nrat", LABELS))) as convert,
            patch(
                "app.domain.model.store_model_artifacts",
                AsyncMock(return_value={"file_hash": "h", "model_path": "p", "labels_path": "l", "file_size_bytes": 1}),
            ),
            patch.object(definitions, "_append_model_status", model_status),
            patch.object(definitions, "update_job", update_job),
        ):
            await definitions.train_species_brain_job("job-1", "user-1", "m1", "org-1", params)

        gcp.assert_awaited_once()
        assert convert.await_args.args[0] == b"zip"
        assert [s[0] for s in statuses] == ["uploaded", "validated"]
        info, fields = statuses[-1][1], statuses[-1][2]
        assert info["trainer"] == "gcp" and info["labels"] == LABELS and fields["label_map"]["rat"]["role"] == "target"
        assert "90% accuracy" in updates[-1]["message"]
