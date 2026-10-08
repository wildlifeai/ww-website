# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""MediaPreparationStep: newest first, a few at a time, progress as it goes (#286)."""

import asyncio

import app.domain.pipeline as pipeline
from app.domain import media_registry
from app.domain.pipeline import PREP_CONCURRENCY, MediaPreparationStep, newest_first
from app.schemas.pipeline import PipelineStepResult, PipelineStepType

DEP = "00000000-0000-0000-0000-0000000000d1"


class _Refused(Exception):
    code = "42501"


def _media(n: int) -> list[dict]:
    # Registered oldest first, as run_pipeline fetches them.
    return [{"id": f"m{i}", "deployment_id": DEP, "file_path": f"gdrive://{i}", "timestamp": f"2026-10-08T00:{i:02d}:00+00:00"} for i in range(n)]


def _fake_prepare(monkeypatch, refuse: set[str] = frozenset()):
    """Record the order photos start in and the most prepared at once."""
    started: list[str] = []
    state = {"in_flight": 0, "peak": 0}

    async def prepare(m):
        started.append(m["id"])
        if m["id"] in refuse:
            raise _Refused("permission denied for table media_assets")
        state["in_flight"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])
        await asyncio.sleep(0.001)
        state["in_flight"] -= 1
        return {"thumbnail_url": "cdn/t.jpg"}

    monkeypatch.setattr(media_registry, "prepare_media_assets_with_retry", prepare)
    return started, state


def test_newest_first_puts_undated_last_and_leaves_the_input_alone():
    media = [
        {"id": "a", "timestamp": "2026-10-01T00:00:00+00:00"},
        {"id": "u", "timestamp": None},
        {"id": "b", "timestamp": "2026-10-02T00:00:00+00:00"},
    ]
    assert [m["id"] for m in newest_first(media)] == ["b", "a", "u"]
    assert [m["id"] for m in media] == ["a", "u", "b"]


async def test_prepares_newest_first_and_a_few_at_a_time(monkeypatch):
    started, state = _fake_prepare(monkeypatch)
    media = _media(10)

    result = await MediaPreparationStep().run(media, DEP, {})

    assert started == [f"m{i}" for i in range(9, -1, -1)]
    assert state["peak"] == PREP_CONCURRENCY
    assert (result.media_processed, result.errors) == (10, 0)


async def test_reports_progress_as_it_goes(monkeypatch):
    _fake_prepare(monkeypatch)
    monkeypatch.setattr(media_registry, "_PROGRESS_EVERY", 2)
    seen: list[tuple[int, int]] = []

    async def progress(done, total):
        seen.append((done, total))

    step = MediaPreparationStep()
    step.on_progress = progress
    await step.run(_media(5), DEP, {})

    assert seen == [(2, 5), (4, 5), (5, 5)]


async def test_stops_on_a_permission_error(monkeypatch):
    # The newest photo is taken first and refused: nothing else starts.
    started, _ = _fake_prepare(monkeypatch, refuse={"m9"})

    result = await MediaPreparationStep().run(_media(10), DEP, {})

    assert started == ["m9"]
    assert result.errors == 10


async def test_photos_in_flight_finish_but_no_new_ones_start_after_a_refusal(monkeypatch):
    started, _ = _fake_prepare(monkeypatch, refuse={"m8"})

    result = await MediaPreparationStep().run(_media(10), DEP, {})

    # m9 was already running when m8 was refused; the six never started count as failed.
    assert started == ["m9", "m8"]
    assert result.errors == 1 + 8


async def test_run_pipeline_hands_each_step_its_progress_callback(monkeypatch):
    class _Query:
        def __getattr__(self, name):
            return self if name == "not_" else (lambda *a, **k: self)

        def execute(self):
            return type("Result", (), {"data": _media(2)})()

    class _Step:
        on_progress = None

        def __init__(self, step_type):
            self.step_type = step_type

        async def run(self, media, deployment_id, config):
            if self.on_progress is not None:
                await self.on_progress(1, len(media))
            return PipelineStepResult(step=self.step_type, media_processed=len(media), duration_seconds=0.1)

    monkeypatch.setattr(pipeline, "create_service_client", lambda: type("Svc", (), {"table": lambda self, name: _Query()})())
    monkeypatch.setattr(pipeline, "get_step", _Step)
    seen: list[tuple[str, int, int]] = []

    async def progress(step, done, total):
        seen.append((step, done, total))

    await pipeline.run_pipeline(deployment_id=DEP, steps=[PipelineStepType.MEDIA_PREP], only_unannotated=False, on_progress=progress)

    assert seen == [("media_prep", 1, 2)]


async def test_auto_annotate_reports_thumbnail_progress(monkeypatch):
    from app.domain import edge_reflection
    from app.jobs import definitions

    async def run(**kwargs):
        await kwargs["on_progress"]("media_prep", 2, 4)

    async def nothing(*args, **kwargs):
        return None

    updates: list[dict] = []

    async def update_job(job_id, **fields):
        updates.append(fields)

    steps = [PipelineStepType.MEDIA_PREP, PipelineStepType.SPECIESNET]
    monkeypatch.setattr(definitions, "build_pipeline_steps", lambda: steps)
    monkeypatch.setattr(edge_reflection, "reflect_edge_deployment", nothing)
    monkeypatch.setattr(pipeline, "run_pipeline", run)
    monkeypatch.setattr(definitions, "emit_detection_notifications", nothing)
    monkeypatch.setattr(definitions, "auto_embed_deployment", nothing)
    monkeypatch.setattr(definitions, "update_job", update_job)

    await definitions.auto_annotate_deployments([DEP], job_id="job-1")

    # Half the thumbnails of the first of two steps: a quarter of the way.
    assert updates[0]["progress"] == 0.25
    assert updates[0]["message"] == "🔬 Preparing thumbnails, 2 of 4, deployment 1/1"
