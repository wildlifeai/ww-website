# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Seconds per frame for every pipeline step and for the whole run (#171)."""

import pytest
from structlog.testing import capture_logs

import app.domain.pipeline as pipeline
from app.schemas.pipeline import PipelineStepResult, PipelineStepType


def test_seconds_per_frame_is_duration_over_frames_and_none_for_no_frames():
    assert PipelineStepResult(step=PipelineStepType.SPECIESNET, media_processed=4, duration_seconds=6.0).seconds_per_frame == 1.5
    assert PipelineStepResult(step=PipelineStepType.SPECIESNET, media_processed=0, duration_seconds=0.4).seconds_per_frame is None
    # Serialised with the result, so it reaches API responses and stored run summaries.
    assert PipelineStepResult(step=PipelineStepType.BIOCLIP, media_processed=2, duration_seconds=1.0).model_dump()["seconds_per_frame"] == 0.5


class _Query:
    """Enough of the Supabase query builder for run_pipeline: media rows in, inserts accepted."""

    def __init__(self, rows):
        self.rows = rows

    def __getattr__(self, name):  # select / eq / in_ / is_ / insert / order / range ...
        if name == "not_":  # a property in postgrest, not a method
            return self
        return lambda *a, **k: self

    def execute(self):
        return type("Result", (), {"data": self.rows})()


class _Svc:
    def __init__(self, media):
        self.media = media

    def table(self, name):
        return _Query(self.media if name == "media" else [])


class _Step:
    def __init__(self, step_type, seconds):
        self.step_type, self.seconds = step_type, seconds

    async def run(self, media, deployment_id, config):
        return PipelineStepResult(step=self.step_type, media_processed=len(media), duration_seconds=self.seconds)


async def test_every_step_and_the_run_log_seconds_per_frame(monkeypatch):
    dep = "00000000-0000-0000-0000-0000000000d1"  # run_pipeline skips a non-UUID deployment
    media = [{"id": f"m{i}", "deployment_id": dep, "file_path": f"f{i}.jpg"} for i in range(4)]
    seconds = {PipelineStepType.MEDIA_PREP: 0.4, PipelineStepType.SPECIESNET: 6.0, PipelineStepType.BIOCLIP: 2.0}
    monkeypatch.setattr(pipeline, "create_service_client", lambda: _Svc(media))
    monkeypatch.setattr(pipeline, "get_step", lambda t: _Step(t, seconds[t]))

    with capture_logs() as logs:
        result = await pipeline.run_pipeline(deployment_id=dep, steps=list(seconds), only_unannotated=False)

    timing = {e["step"]: e for e in logs if e["event"] == "pipeline_step_timing"}
    assert {s: (e["seconds_per_frame"], e["gpu_model"]) for s, e in timing.items()} == {
        "media_prep": (0.1, False),
        "speciesnet": (1.5, True),
        "bioclip": (0.5, True),
    }
    complete = next(e for e in logs if e["event"] == "pipeline_complete")
    assert complete["seconds_per_frame"] == pytest.approx(complete["duration_seconds"] / 4, abs=0.01)
    assert [s.seconds_per_frame for s in result.steps] == [0.1, 1.5, 0.5]
