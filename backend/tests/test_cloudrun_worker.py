# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ML worker as a Cloud Run job: dispatch precedence and fallback, and the entry point."""

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.jobs.cloudrun_entry as entry
import app.jobs.dispatch as dispatch


@pytest.fixture
def cloud_run(monkeypatch):
    """Configure the Cloud Run route and capture every run_job request."""
    monkeypatch.setattr(dispatch.settings, "CLOUD_RUN_JOB_NAME", "ww-ml-worker-dev", raising=False)
    monkeypatch.setattr(dispatch.settings, "GOOGLE_CLOUD_PROJECT", "ww-pilot-dev", raising=False)
    monkeypatch.setattr(dispatch.settings, "CLOUD_RUN_JOB_REGION", "asia-southeast1", raising=False)
    monkeypatch.setattr(dispatch.settings, "REDIS_URL", "", raising=False)
    requests = []

    class _Client:
        def run_job(self, request):
            requests.append(request)
            return SimpleNamespace(metadata=SimpleNamespace(name="executions/ww-ml-worker-dev-abc"))

    monkeypatch.setattr(dispatch, "_jobs_client", lambda: _Client())
    return requests


@pytest.fixture
def fallback(monkeypatch):
    calls = []

    async def _fallback(name, *args, **kwargs):
        calls.append((name, args, kwargs))
        return "arq"

    monkeypatch.setattr(dispatch, "_enqueue_arq_or_local", _fallback)
    return calls


async def test_cloud_run_comes_first_and_carries_the_job(cloud_run, fallback):
    mode = await dispatch.enqueue_job("annotate_deployments_job", "job1", ["dep1"], "user1", force=True)

    assert mode == "cloudrun"
    assert fallback == []
    request = cloud_run[0]
    assert request.name == "projects/ww-pilot-dev/locations/asia-southeast1/jobs/ww-ml-worker-dev"
    args = list(request.overrides.container_overrides[0].args)
    assert args[:3] == ["-m", "app.jobs.cloudrun_entry", "annotate_deployments_job"]
    assert entry.parse_payload(args[3]) == (["job1", ["dep1"], "user1"], {"force": True})


async def test_a_failed_start_falls_through_with_the_original_kwargs(cloud_run, fallback, monkeypatch):
    class _Refusing:
        def run_job(self, request):
            raise RuntimeError("403 run.jobs.run denied")

    monkeypatch.setattr(dispatch, "_jobs_client", lambda: _Refusing())

    mode = await dispatch.enqueue_job("embed_deployment_job", "job1", "dep1", _job_id="dedup")

    assert mode == "arq"
    assert fallback == [("embed_deployment_job", ("job1", "dep1"), {"_job_id": "dedup"})]


async def test_an_oversized_payload_takes_the_fallback(cloud_run, fallback):
    media_ids = [f"{i:036d}" for i in range(2000)]  # a large CamtrapDP import

    mode = await dispatch.enqueue_job("annotate_deployments_job", "job1", ["dep1"], "user1", True, media_ids)

    assert mode == "arq"
    assert cloud_run == []


async def test_defer_by_starts_the_execution_later(cloud_run, fallback, monkeypatch):
    scheduled, slept = [], []
    monkeypatch.setattr("app.jobs.runner.enqueue_local_job", scheduled.append)

    async def _sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(dispatch.asyncio, "sleep", _sleep)

    mode = await dispatch.enqueue_job("annotate_deployments_job", "job1", ["dep1"], "user1", _defer_by=timedelta(seconds=60))

    assert mode == "cloudrun"
    assert cloud_run == []  # nothing started yet: the api_jobs row stays queued for coalescing
    await scheduled[0]
    assert slept == [60.0]
    assert len(cloud_run) == 1


async def test_a_deferred_start_that_fails_falls_back_without_waiting_again(cloud_run, fallback, monkeypatch):
    scheduled = []
    monkeypatch.setattr("app.jobs.runner.enqueue_local_job", scheduled.append)
    monkeypatch.setattr(dispatch.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(dispatch.settings, "GOOGLE_CLOUD_PROJECT", "", raising=False)

    await dispatch.enqueue_job("annotate_deployments_job", "job1", ["dep1"], _defer_by=60)
    await scheduled[0]

    assert fallback == [("annotate_deployments_job", ("job1", ["dep1"]), {})]


async def test_without_a_job_name_redis_and_local_are_unchanged(fallback, monkeypatch):
    monkeypatch.setattr(dispatch.settings, "CLOUD_RUN_JOB_NAME", "", raising=False)

    assert await dispatch.enqueue_job("recompute_al_job", "job1", "dep1") == "arq"
    assert fallback == [("recompute_al_job", ("job1", "dep1"), {})]


def test_job_offload_configured(monkeypatch):
    s = dispatch.settings
    monkeypatch.setattr(s, "REDIS_URL", "", raising=False)
    monkeypatch.setattr(s, "CLOUD_RUN_JOB_NAME", "", raising=False)
    assert s.job_offload_configured is False
    monkeypatch.setattr(s, "CLOUD_RUN_JOB_NAME", "ww-ml-worker-dev", raising=False)
    assert s.job_offload_configured is True


# ── The entry point ───────────────────────────────────────────────────


def test_entry_runs_a_registered_job_and_flushes(monkeypatch):
    seen = []

    async def annotate_deployments_job(*args, **kwargs):
        seen.append((args, kwargs))

    flush = AsyncMock()
    monkeypatch.setattr(entry, "resolve_job", lambda name: annotate_deployments_job if name == "annotate_deployments_job" else None)
    monkeypatch.setattr("app.jobs.store.flush_pending_syncs", flush)

    payload = dispatch.cloud_run_payload(("job1", ["dep1"]), {"force": True})
    assert entry.main(["annotate_deployments_job", payload]) == 0
    assert seen == [(("job1", ["dep1"]), {"force": True})]
    flush.assert_awaited_once()


def test_entry_exits_1_when_the_job_raises(monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("supabase unreachable")

    monkeypatch.setattr(entry, "resolve_job", lambda name: broken)
    monkeypatch.setattr("app.jobs.store.flush_pending_syncs", AsyncMock())

    assert entry.main(["annotate_deployments_job", "{}"]) == 1


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["annotate_deployments_job"],
        ["not_a_job", "{}"],
        ["annotate_deployments_job", "not json"],
        ["annotate_deployments_job", json.dumps([1, 2])],
        ["annotate_deployments_job", json.dumps({"args": "job1"})],
    ],
)
def test_entry_exits_2_on_bad_arguments(argv):
    assert entry.main(argv) == 2


def test_only_registered_jobs_resolve():
    assert entry.resolve_job("annotate_deployments_job").__name__ == "annotate_deployments_job"
    assert entry.resolve_job("auto_annotate_deployments") is None  # a helper, not a job
