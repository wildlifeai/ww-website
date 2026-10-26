# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Thumbnails for an upload whose AI does not run: they are the pipeline's first step, so the
upload job starts the thumbnails job itself, one per deployment, shared by the upload's chunks."""

import asyncio

import pytest

from app.jobs import definitions
from app.schemas.pipeline import PipelineStepType

DEP = "aaaaaaaa-0000-4000-8000-000000000001"
STEPS = [PipelineStepType.MEDIA_PREP, PipelineStepType.SPECIESNET]


@pytest.fixture
def registry_on(monkeypatch):
    monkeypatch.setattr("app.config.settings.FF_MEDIA_REGISTRY_ENABLED", True)


@pytest.mark.parametrize(
    "deps, steps, run_ai, expected",
    [
        ([DEP], STEPS, True, False),  # the AI runs, and makes them first
        ([DEP], STEPS, False, True),  # "Run AI" off on the upload
        ([DEP], [], True, True),  # no AI configured on this server
        ([], STEPS, False, False),  # no real deployment in the upload
    ],
)
def test_an_upload_needs_its_own_thumbnails_job_only_when_the_ai_does_not_run(registry_on, deps, steps, run_ai, expected):
    assert definitions.thumbnails_need_their_own_job(deps, steps, run_ai) is expected


def test_no_thumbnails_job_without_the_media_registry(monkeypatch):
    monkeypatch.setattr("app.config.settings.FF_MEDIA_REGISTRY_ENABLED", False)
    assert definitions.thumbnails_need_their_own_job([DEP], [], False) is False


@pytest.fixture
def jobs(monkeypatch):
    """Fakes for the job store, the local runner and the backfill itself."""
    created: list[dict] = []
    started: list = []
    ran: list[tuple[str, str]] = []

    async def create_job(**kwargs):
        created.append(kwargs)
        return f"job-{len(created)}"

    async def backfill(job_id, deployment_id):
        ran.append((job_id, deployment_id))

    monkeypatch.setattr(definitions, "create_job", create_job)
    monkeypatch.setattr(definitions, "backfill_thumbnails_job", backfill)
    monkeypatch.setattr("app.jobs.runner.enqueue_local_job", started.append)
    monkeypatch.setattr(definitions, "_pending_thumbnails", {})
    return created, started, ran


async def test_the_upload_chunks_share_one_waiting_job(jobs):
    created, started, ran = jobs
    first = await definitions.start_thumbnail_backfill(DEP, "user-1", delay_s=0.01)
    second = await definitions.start_thumbnail_backfill(DEP, "user-1", delay_s=0.01)
    assert first == second == "job-1"
    assert created == [{"user_id": "user-1", "kind": "media_prep", "label": f"Thumbnails for deployment {DEP[:8]}", "deployment_ids": [DEP]}]
    assert len(started) == 1
    await asyncio.gather(*started)
    assert ran == [("job-1", DEP)]


async def test_a_chunk_after_the_job_started_gets_a_follow_up(jobs):
    created, started, ran = jobs
    await definitions.start_thumbnail_backfill(DEP, "user-1")
    await started.pop()  # the first job runs, so it no longer waits for the deployment
    follow_up = await definitions.start_thumbnail_backfill(DEP, "user-1")
    assert follow_up == "job-2" and len(created) == 2
    await asyncio.gather(*started)
    assert ran == [("job-1", DEP), ("job-2", DEP)]
