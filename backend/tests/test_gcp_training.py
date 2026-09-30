# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""services/gcp_training.py: GCS layout, job parameters, state mapping, and the run cycle with fake SDK clients."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import gcp_training as g
from app.services.gcp_training import RunState, TrainerError, TrainerNotConfigured
from tests.tflite_fixtures import write_artifact_dir

OBJECTS = [("images/00000.jpg", b"n1"), ("images/00001.jpg", b"r1"), ("manifest.json", b'{"labels": ["not rat", "rat"]}')]


class TestPureHelpers:
    def test_layout_and_environment(self):
        assert g.dataset_prefix("k") == "runs/k/dataset" and g.output_prefix("k") == "runs/k/output"
        assert g.marker_object("k") == "runs/k/execution.json"
        assert g.job_environment("ww-training-dev", "k") == {
            "TRAINING_INPUT_URI": "gs://ww-training-dev/runs/k/dataset",
            "TRAINING_OUTPUT_URI": "gs://ww-training-dev/runs/k/output",
        }
        assert g.job_resource_name("p", "asia-southeast1", "j") == "projects/p/locations/asia-southeast1/jobs/j"

    def test_execution_state(self):
        def state(**kw):
            base = dict(succeeded=0, failed=0, cancelled=0, running=0, completed=False)
            return g.execution_state(**{**base, **kw})

        assert state().state == RunState.QUEUED
        assert state(running=1).state == RunState.RUNNING
        assert state(succeeded=1, completed=True).state == RunState.SUCCEEDED
        failed = state(failed=1, completed=True, reasons="OOM")
        assert failed.state == RunState.FAILED and "OOM" in failed.message
        assert "cancelled" in state(cancelled=1, completed=True).message
        assert state(completed=True).state == RunState.FAILED

    def test_status_from_execution_keeps_only_failed_conditions(self):
        ex = SimpleNamespace(
            succeeded_count=0,
            failed_count=1,
            cancelled_count=0,
            running_count=0,
            completion_time="t",
            conditions=[
                SimpleNamespace(message="task exited 1", state=SimpleNamespace(name="CONDITION_FAILED")),
                SimpleNamespace(message="ok", state="CONDITION_SUCCEEDED"),
            ],
        )
        s = g._status_from_execution(ex)
        assert s.state == RunState.FAILED and "task exited 1" in s.message and "ok" not in s.message

    def test_configuration_errors_name_the_setting(self):
        with pytest.raises(TrainerNotConfigured, match="GOOGLE_CLOUD_PROJECT, GCS_TRAINING_BUCKET"):
            g.GcpCloudRunTrainer(project="", region="asia-southeast1", bucket="", job="j")
        with pytest.raises(TrainerNotConfigured, match="3600"):
            g.GcpCloudRunTrainer(project="p", region="r", bucket="b", job="j", run_timeout_s=7200)

    def test_from_settings_uses_the_migration_plan_names(self):
        s = SimpleNamespace(
            GOOGLE_CLOUD_PROJECT="ww-pilot-dev",
            CLOUD_RUN_JOB_REGION="asia-southeast1",
            GCS_TRAINING_BUCKET="ww-training-dev",
            TRAINING_JOB_NAME="ww-species-trainer",
            TRAINING_RUN_TIMEOUT_S=1800,
        )
        t = g.GcpCloudRunTrainer.from_settings(s)
        assert (t.project, t.region, t.bucket_name, t.run_timeout_s) == ("ww-pilot-dev", "asia-southeast1", "ww-training-dev", 1800)


# ── The run cycle against fake SDK clients ────────────────────────────


class FakeBlob:
    def __init__(self, store, name):
        self.store, self.name = store, name

    def exists(self):
        return self.name in self.store

    def upload_from_string(self, data, content_type=None):
        self.store[self.name] = data if isinstance(data, bytes) else data.encode("utf-8")

    def download_as_bytes(self):
        return self.store[self.name]

    def download_to_filename(self, path):
        Path(path).write_bytes(self.store[self.name])


class FakeBucket:
    def __init__(self, store):
        self.store = store

    def blob(self, name):
        return FakeBlob(self.store, name)

    def list_blobs(self, prefix=""):
        return [FakeBlob(self.store, n) for n in sorted(self.store) if n.startswith(prefix)]


class FakeJobs:
    def __init__(self):
        self.requests = []

    def run_job(self, request):
        self.requests.append(request)
        return SimpleNamespace(metadata=SimpleNamespace(name=f"{request['name']}/executions/ex-{len(self.requests)}", log_uri="https://console/logs"))


class FakeExecutions:
    def __init__(self, script):
        self.script = list(script)

    def get_execution(self, name):
        counts = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        return SimpleNamespace(conditions=[], **counts)


def _counts(**kw):
    return {"succeeded_count": 0, "failed_count": 0, "cancelled_count": 0, "running_count": 0, "completion_time": None, **kw}


@pytest.fixture
def fakes(monkeypatch):
    store = {}
    jobs = FakeJobs()
    execs = FakeExecutions([_counts(), _counts(running_count=1), _counts(succeeded_count=1, completion_time="t")])
    monkeypatch.setattr(g, "_storage_client", lambda project: SimpleNamespace(bucket=lambda name: FakeBucket(store)))
    monkeypatch.setattr(g, "_jobs_client", lambda: jobs)
    monkeypatch.setattr(g, "_executions_client", lambda: execs)
    monkeypatch.setattr(g, "_run_job_request", lambda job_name, env, timeout_s: {"name": job_name, "env": env, "timeout": timeout_s})
    return SimpleNamespace(store=store, jobs=jobs)


def _trainer():
    return g.GcpCloudRunTrainer(
        project="ww-pilot-dev", region="asia-southeast1", bucket="ww-training-dev", job="ww-species-trainer", run_timeout_s=3000
    )


def _finish(store, tmp_path, run_key="k"):
    for p in write_artifact_dir(tmp_path / "src", labels=["not rat", "rat"]).iterdir():
        store[f"runs/{run_key}/output/{p.name}"] = p.read_bytes()


class TestTrainerCycle:
    async def test_submit_uploads_and_launches(self, fakes):
        await _trainer().submit("k", OBJECTS)
        assert fakes.store["runs/k/dataset/images/00001.jpg"] == b"r1" and "runs/k/dataset/manifest.json" in fakes.store
        req = fakes.jobs.requests[0]
        assert req["name"] == "projects/ww-pilot-dev/locations/asia-southeast1/jobs/ww-species-trainer" and req["timeout"] == 3000
        assert req["env"]["TRAINING_OUTPUT_URI"] == "gs://ww-training-dev/runs/k/output"
        assert json.loads(fakes.store["runs/k/execution.json"])["execution"].endswith("/executions/ex-1")

    async def test_poll_survives_a_worker_restart(self, fakes):
        await _trainer().submit("k", OBJECTS)
        fresh = _trainer()  # no memory of the submit: reads the marker
        assert (await fresh.poll("k")).state == RunState.QUEUED
        running = await fresh.poll("k")
        assert running.state == RunState.RUNNING and running.detail["log_uri"]

    async def test_poll_without_marker(self, fakes):
        with pytest.raises(TrainerError, match="no execution recorded"):
            await _trainer().poll("never")

    async def test_resubmit_reuses_a_finished_output(self, fakes, tmp_path):
        _finish(fakes.store, tmp_path)
        await _trainer().submit("k", OBJECTS)
        assert fakes.jobs.requests == []

    async def test_fetch_empty_output(self, fakes, tmp_path):
        with pytest.raises(TrainerError, match="no output"):
            await _trainer().fetch_output("k", tmp_path / "dest")

    async def test_full_cycle(self, fakes, tmp_path):
        ticks = []

        async def tick(msg):
            ticks.append(msg)
            if len(ticks) == 2:  # the container finishes while we poll
                _finish(fakes.store, tmp_path)

        out = await _trainer().run("k", OBJECTS, tmp_path / "dest", poll_interval_s=0, on_tick=tick)
        assert (out / "labels.txt").read_text() == "not rat\nrat\n" and len(fakes.jobs.requests) == 1

    async def test_failure_and_timeout(self, fakes, tmp_path, monkeypatch):
        monkeypatch.setattr(g, "_executions_client", lambda: FakeExecutions([_counts(failed_count=1, completion_time="t")]))
        with pytest.raises(TrainerError, match="failed.*logs: https://console/logs"):
            await _trainer().run("k", OBJECTS, tmp_path, poll_interval_s=0)
        monkeypatch.setattr(g, "_executions_client", lambda: FakeExecutions([_counts(running_count=1)]))
        with pytest.raises(TrainerError, match="did not finish"):
            await _trainer().run("k2", OBJECTS, tmp_path, poll_interval_s=0, timeout_s=0)
