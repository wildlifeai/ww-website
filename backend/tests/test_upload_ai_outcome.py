# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The upload job's final message says what its AI did (#432).

"AI analysis complete" only when the AI ran in the upload job; "AI analysis queued" when another
job runs it, with the wait for the next GPU run when that job is on the REANNZ batch, worded as
the upload dock words it; "AI not run" with the reason otherwise. The outcome is also the job's
``result``, which the dock reads, and an upload that did not run the AI says why in its log.
Errors keep their wording.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.config import settings
from app.domain import pipeline
from app.jobs import definitions, dispatch, store
from app.schemas.job import JobStatus
from app.schemas.pipeline import PipelineStepType
from app.services import object_store
from tests.fake_postgrest import FakeDB

DEP = "d0000000-0000-4000-8000-000000000432"
AI_JOB = "a1b2c3d4-0000-4000-8000-000000000432"
STEPS = [PipelineStepType.SPECIESNET]


@pytest.mark.parametrize(
    ("minutes", "words"),
    [(60, "an hour"), (300, "5 hours"), (120, "2 hours"), (90, "90 minutes"), (45, "45 minutes"), (1, "a minute")],
)
def test_a_wait_is_worded_as_the_website_words_it(minutes, words):
    assert store.wait_in_words(minutes) == words


@pytest.mark.parametrize(
    ("deps", "steps", "run_ai", "queued", "result", "words"),
    [
        ([DEP], STEPS, True, None, {"ai": "ran"}, "AI analysis complete"),
        ([DEP], STEPS, True, (AI_JOB, "arq"), {"ai": "queued", "ai_job_id": AI_JOB}, "AI analysis queued (job a1b2c3d4)"),
        ([DEP], STEPS, True, (AI_JOB, "local"), {"ai": "queued", "ai_job_id": AI_JOB}, "AI analysis queued (job a1b2c3d4)"),
        (
            [DEP],
            STEPS,
            True,
            (AI_JOB, "batch"),
            {"ai": "queued", "ai_job_id": AI_JOB},
            "AI analysis queued for the next GPU run, usually within 5 hours (job a1b2c3d4)",
        ),
        ([DEP], STEPS, False, None, {"ai": "not_run", "ai_reason": "turned off for this upload"}, "AI not run (turned off for this upload)"),
        ([], STEPS, True, None, {"ai": "not_run", "ai_reason": "no photo matched a deployment"}, "AI not run (no photo matched a deployment)"),
        ([DEP], [], True, None, {"ai": "not_run", "ai_reason": "no AI step enabled"}, "AI not run (no AI step enabled)"),
        # The uploader's own choice is the reason given when more than one applies.
        ([], [], False, None, {"ai": "not_run", "ai_reason": "turned off for this upload"}, "AI not run (turned off for this upload)"),
    ],
)
def test_the_outcome_says_what_the_ai_did(monkeypatch, deps, steps, run_ai, queued, result, words):
    monkeypatch.setattr(settings, "BATCH_MAX_WAIT_MINUTES", 60)
    monkeypatch.setattr(settings, "BATCH_CRON_INTERVAL_MINUTES", 240)
    assert definitions.upload_ai_outcome(deps, steps, run_ai, queued) == (result, words)


def test_a_batch_with_no_stated_wait_says_only_the_next_gpu_run(monkeypatch):
    monkeypatch.setattr(settings, "BATCH_MAX_WAIT_MINUTES", 0)
    monkeypatch.setattr(settings, "BATCH_CRON_INTERVAL_MINUTES", 0)
    _, words = definitions.upload_ai_outcome([DEP], STEPS, True, (AI_JOB, "batch"))
    assert words == "AI analysis queued for the next GPU run (job a1b2c3d4)"


# ── The whole upload job, with the archive, the pipeline and the dispatch faked ──


class _Archive:
    """The archive's ``upload_analysis_images``: reports each file, optionally failing the first."""

    def __init__(self, fail_first: bool = False):
        self.fail_first = fail_first

    async def upload_analysis_images(self, files, file_callback):
        for i, f in enumerate(files, 1):
            action = "failed" if self.fail_first and i == 1 else "uploaded"
            await file_callback(action, filename=f["filename"], index=i, total=len(files), error="quota")
        return {"files": []}


@pytest.fixture
def upload(monkeypatch):
    """Run ``upload_drive_images_job`` on two photos of ``dep``; returns ``run(...) -> JobInfo``."""
    store._memory_store.clear()
    store._memory_events.clear()
    fake = FakeDB({"api_jobs": []})
    monkeypatch.setattr(store, "create_service_client", fake.client)
    monkeypatch.setattr("app.services.supabase_client.create_service_client", lambda: MagicMock())

    async def retrieve(blob_id):
        return b"jpeg", "image/jpeg"

    async def noop(*_a, **_k):
        return None

    monkeypatch.setattr(object_store, "retrieve_blob", retrieve)
    monkeypatch.setattr(object_store, "delete_blob", noop)
    monkeypatch.setattr(definitions, "start_thumbnail_backfill", noop)
    monkeypatch.setattr(definitions, "emit_detection_notifications", noop)
    monkeypatch.setattr(definitions, "build_pipeline_steps", lambda: STEPS)
    monkeypatch.setattr(settings, "FF_WILDLIFE_BRAIN_ENABLED", False)
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(settings, "WORKER_BACKEND", "local")
    monkeypatch.setattr(settings, "BATCH_MAX_WAIT_MINUTES", 60)
    monkeypatch.setattr(settings, "BATCH_CRON_INTERVAL_MINUTES", 240)

    async def run(dep: str = DEP, run_ai: bool = True, fail_first: bool = False):
        monkeypatch.setattr(object_store, "archive_service", lambda: _Archive(fail_first))
        job_id = await store.create_job(user_id="u1", kind="upload", label="Upload")
        files = [{"blob_id": f"b{i}", "filename": f"{i}.jpg", "deployment": {"id": dep}, "project": None} for i in range(2)]
        await definitions.upload_drive_images_job(job_id, {"files": files, "user_id": "u1", "run_ai": run_ai})
        job = await store.get_job(job_id)
        assert job is not None
        return job

    yield run
    store._memory_store.clear()
    store._memory_events.clear()


def _log(job) -> list[str]:
    return [e.message for e in job.events]


async def test_an_inline_run_says_the_analysis_is_complete(upload, monkeypatch):
    async def run_pipeline(**_k):
        return SimpleNamespace(total_observations=3)

    monkeypatch.setattr(pipeline, "run_pipeline", run_pipeline)
    job = await upload()
    assert job.status == JobStatus.COMPLETED
    assert job.message == "✅ Done: 2 images synced to Drive, AI analysis complete"
    assert job.result == {"ai": "ran"}


async def test_a_run_handed_to_the_arq_worker_says_queued_with_its_job(upload, monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "redis://worker")

    async def reserve(dep_ids, user_id):
        return AI_JOB, dep_ids, {}, {}

    async def enqueue(name, *args, **kwargs):
        assert name == "annotate_deployments_job" and args[0] == AI_JOB
        return "arq"

    monkeypatch.setattr(definitions, "reserve_ai_job", reserve)
    monkeypatch.setattr(dispatch, "enqueue_job", enqueue)
    job = await upload()
    assert job.status == JobStatus.COMPLETED
    assert job.message == "✅ Done: 2 images synced to Drive, AI analysis queued (job a1b2c3d4)"
    assert job.result == {"ai": "queued", "ai_job_id": AI_JOB}
    # The log no longer says the AI phase is complete when it was only handed on.
    assert "ai_pipeline complete" not in _log(job)


async def test_a_run_left_for_the_gpu_batch_states_the_wait(upload, monkeypatch):
    """WORKER_BACKEND=lambda, through the real enqueue_job: the AI job waits in api_jobs."""
    monkeypatch.setattr(settings, "WORKER_BACKEND", "lambda")
    made: list[str] = []

    async def reserve(dep_ids, user_id):
        made.append(await store.create_job(user_id=user_id, kind="ai_pipeline", label="AI", deployment_ids=dep_ids))
        return made[0], dep_ids, {}, {}

    monkeypatch.setattr(definitions, "reserve_ai_job", reserve)
    job = await upload()
    ai = await store.get_job(made[0])
    assert ai is not None and ai.batch_wait is not None and ai.batch_wait.usual_wait_minutes == 300
    assert job.message == f"✅ Done: 2 images synced to Drive, AI analysis queued for the next GPU run, usually within 5 hours (job {made[0][:8]})"
    assert job.result == {"ai": "queued", "ai_job_id": made[0]}


async def test_joining_a_queued_batch_job_names_that_job(upload, monkeypatch):
    monkeypatch.setattr(settings, "WORKER_BACKEND", "lambda")

    async def reserve(dep_ids, user_id):
        return None, [], {d: AI_JOB for d in dep_ids}, {}

    monkeypatch.setattr(definitions, "reserve_ai_job", reserve)
    job = await upload()
    assert job.message == "✅ Done: 2 images synced to Drive, AI analysis queued for the next GPU run, usually within 5 hours (job a1b2c3d4)"
    assert job.result == {"ai": "queued", "ai_job_id": AI_JOB}


@pytest.mark.parametrize(
    ("kwargs", "steps", "reason"),
    [
        ({"run_ai": False}, STEPS, "turned off for this upload"),
        ({"dep": "00000000"}, STEPS, "no photo matched a deployment"),  # a card folder, not a deployment
        ({}, [], "no AI step enabled"),
    ],
)
async def test_an_upload_that_did_not_run_the_ai_says_why(upload, monkeypatch, kwargs, steps, reason):
    monkeypatch.setattr(definitions, "build_pipeline_steps", lambda: steps)
    monkeypatch.setattr(settings, "REDIS_URL", "redis://worker")  # offload configured, nothing queued

    async def must_not_run(*_a, **_k):
        raise AssertionError("the AI must not be dispatched")

    monkeypatch.setattr(definitions, "reserve_ai_job", must_not_run)
    job = await upload(**kwargs)
    assert job.status == JobStatus.COMPLETED
    assert job.message == f"✅ Done: 2 images synced to Drive, AI not run ({reason})"
    assert job.result == {"ai": "not_run", "ai_reason": reason}
    assert f"⏭️ AI not run ({reason})" in _log(job)


async def test_an_ai_error_keeps_its_wording(upload, monkeypatch):
    async def run_pipeline(**_k):
        raise RuntimeError("model missing")

    monkeypatch.setattr(pipeline, "run_pipeline", run_pipeline)
    job = await upload()
    assert job.status == JobStatus.COMPLETED_WITH_ERRORS
    assert job.message == "⚠️ Done with issues — 2 uploaded, 0 skipped, 1 AI error(s)"


async def test_a_drive_error_keeps_its_wording(upload, monkeypatch):
    job = await upload(run_ai=False, fail_first=True)
    assert job.status == JobStatus.COMPLETED_WITH_ERRORS
    assert job.message == "⚠️ Done with issues — 1 uploaded, 0 skipped, 1 Drive error(s)"
    assert job.result == {"ai": "not_run", "ai_reason": "turned off for this upload"}
