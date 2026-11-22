# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""``POST /api/pipeline/run`` answers with a job (#452).

The route queues an ``ai_run`` job and returns at once; ``annotate_deployments_job`` runs the
request's own steps and settings through ``run_pipeline`` (its deployment lock included), and
under ``WORKER_BACKEND=lambda`` the job waits for the REANNZ batch, whose lease and ingest use
the same settings.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings
from app.domain import batch_lease, pipeline
from app.domain.pipeline import PipelineBusyError, deployment_lock_name
from app.jobs import definitions, ingest, pipeline_run, store
from app.jobs.pipeline_run import AI_RUN_KIND, AnnotateRun, start_pipeline_run
from app.schemas.job import JobStatus
from app.schemas.pipeline import PipelineRunRequest, PipelineRunResult, PipelineStepType
from app.services import inference_cache, locks, object_store

DEP = "d0000000-0000-4000-8000-000000000452"
JOB = "a0000000-0000-4000-8000-000000000452"
LEASE = "1ea5e000-0000-4000-8000-000000000452"
SPEC = {
    "steps": ["speciesnet", "bioclip"],
    "confidence_threshold": 0.6,
    "config": {"bioclip_labels": ["Rattus rattus"], "whole_frame_area": 0.5},
    "only_unannotated": False,
}
REAL_RUN_PIPELINE = pipeline.run_pipeline


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """No Redis and no database behind services.locks, and a local object store."""
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(settings, "WORKER_BACKEND", "local")
    monkeypatch.setattr(locks, "_lease_client", lambda: None)
    monkeypatch.setattr(settings, "OBJECT_STORE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_OBJECT_STORE_DIR", str(tmp_path / "objects"))
    # No flag-built steps unless a test switches them on (flags_on).
    monkeypatch.setattr(settings, "FF_ML_ENABLED", False)


@pytest.fixture
def api_jobs(monkeypatch):
    """The ``api_jobs`` rows start_pipeline_run reads and writes."""
    rows: dict[str, dict] = {}

    async def find_active(kind="ai_pipeline"):
        await asyncio.sleep(0)  # the Supabase round trip, so concurrent requests interleave
        return [{"job_id": k, **v} for k, v in rows.items() if v["kind"] == kind and v["status"] in ("queued", "processing")]

    async def create_job(user_id=None, kind=None, label=None, deployment_ids=None):
        await asyncio.sleep(0)
        job_id = f"job{len(rows)}"
        rows[job_id] = {"status": "queued", "kind": kind, "label": label, "deployment_ids": deployment_ids, "error": None}
        return job_id

    async def update_job(job_id, status=None, error=None, **_k):
        rows[job_id].update(status=status.value if status else rows[job_id]["status"], error=error)

    monkeypatch.setattr(pipeline_run, "find_active_ai_jobs", find_active)
    monkeypatch.setattr(pipeline_run, "create_job", create_job)
    monkeypatch.setattr(pipeline_run, "flush_pending_syncs", AsyncMock())
    monkeypatch.setattr(pipeline_run, "update_job", update_job)
    return rows


async def _post(monkeypatch, body: PipelineRunRequest):
    from app.routers import pipeline as route

    monkeypatch.setattr(route, "assert_write", AsyncMock())
    # The undecorated handler: the router is flag-gated out of the test app and the limiter needs a real request.
    handler = route.run_inference_pipeline.__wrapped__
    return await handler(SimpleNamespace(state=SimpleNamespace(request_id="r")), body, SimpleNamespace(id="u1"))


# ── the route ────────────────────────────────────────────────────────


async def test_the_route_answers_at_once_with_a_queued_job(monkeypatch, api_jobs):
    enqueue = AsyncMock(return_value="arq")
    monkeypatch.setattr(pipeline_run, "enqueue_job", enqueue)

    response = await _post(monkeypatch, PipelineRunRequest(deployment_id=DEP, **SPEC))

    assert response.error is None
    assert response.data == {"job_id": "job0", "status": "queued", "deployment_id": DEP}
    assert api_jobs["job0"]["kind"] == AI_RUN_KIND and api_jobs["job0"]["deployment_ids"] == [DEP]
    enqueue.assert_awaited_once_with("annotate_deployments_job", "job0", [DEP], "u1", False, None, pipeline=SPEC)


@pytest.mark.parametrize("kind", ["ai_pipeline", AI_RUN_KIND])
async def test_the_route_says_busy_while_an_ai_job_is_on_the_deployment(monkeypatch, api_jobs, kind):
    api_jobs["upload"] = {"status": "queued", "kind": kind, "deployment_ids": [DEP]}
    enqueue = AsyncMock()
    monkeypatch.setattr(pipeline_run, "enqueue_job", enqueue)

    response = await _post(monkeypatch, PipelineRunRequest(deployment_id=DEP))

    assert response.error.code == "PIPELINE_BUSY" and response.error.retryable is True
    assert list(api_jobs) == ["upload"] and enqueue.await_count == 0


async def test_the_route_refuses_a_step_with_no_implementation(monkeypatch, api_jobs):
    response = await _post(monkeypatch, PipelineRunRequest(deployment_id=DEP, steps=[PipelineStepType.CUSTOM]))
    assert response.error.code == "INVALID_REQUEST" and api_jobs == {}


async def test_two_clicks_together_make_one_job(monkeypatch, api_jobs):
    monkeypatch.setattr(pipeline_run, "enqueue_job", AsyncMock())
    results = await asyncio.gather(*(start_pipeline_run(PipelineRunRequest(deployment_id=DEP), "u1") for _ in range(5)), return_exceptions=True)
    assert [r for r in results if isinstance(r, str)] == ["job0"]
    assert sum(isinstance(r, PipelineBusyError) for r in results) == 4


async def test_a_run_that_cannot_be_queued_fails_its_job(monkeypatch, api_jobs):
    monkeypatch.setattr(pipeline_run, "enqueue_job", AsyncMock(side_effect=ValueError("No api_jobs row for batch job job0")))
    with pytest.raises(ValueError):
        await start_pipeline_run(PipelineRunRequest(deployment_id=DEP), "u1")
    assert api_jobs["job0"]["status"] == "failed" and "No api_jobs row" in api_jobs["job0"]["error"]


async def test_on_lambda_the_run_waits_for_the_batch_and_nothing_runs_in_the_process(monkeypatch, api_jobs):
    monkeypatch.setattr(settings, "WORKER_BACKEND", "lambda")
    monkeypatch.setattr(settings, "CPU_FUNCTION_NAME", "ww-dev-cpu")
    recorded: dict[str, dict] = {}

    async def set_job_dispatch(job_id, spec):
        recorded[job_id] = spec
        return True

    monkeypatch.setattr(store, "set_job_dispatch", set_job_dispatch)
    local = MagicMock()
    monkeypatch.setattr("app.jobs.runner.enqueue_local_job", local)
    invoke = AsyncMock()
    monkeypatch.setattr("app.services.lambda_invoke.invoke_job", invoke)

    job_id = await start_pipeline_run(PipelineRunRequest(deployment_id=DEP, **SPEC), "u1")

    spec = recorded[job_id]
    assert spec["name"] == "annotate_deployments_job" and spec["kind"] == "annotate"
    assert spec["args"] == [[DEP], "u1", False, None] and spec["kwargs"] == {"pipeline": SPEC}
    json.dumps(spec)  # api_jobs.job_data is jsonb
    local.assert_not_called()
    invoke.assert_not_awaited()


# ── the grid's Run AI again (POST /api/media/run-selected) ───────────

DEP_B = "d0000000-0000-4000-8000-000000000453"
SELECTED = {"steps": ["speciesnet", "animal_crop", "bioclip"], "confidence_threshold": 0.2, "config": {}, "only_unannotated": False}


@pytest.fixture
def run_selected(monkeypatch, api_jobs):
    """The route with the photos' rows in a fake PostgREST and every photo but ``not-mine`` writable."""
    from app.routers import media as route
    from tests.fake_postgrest import FakeDB

    db = FakeDB({"media": [{"id": "m1", "deployment_id": DEP}, {"id": "m2", "deployment_id": DEP_B}, {"id": "m3", "deployment_id": DEP}]})
    monkeypatch.setattr(pipeline_run, "create_service_client", db.client)
    monkeypatch.setattr(settings, "FF_PIPELINE_ENABLED", True)

    async def writable(user_id, media_ids):
        return [m for m in media_ids if m != "not-mine"]

    monkeypatch.setattr(route, "writable_media_ids", writable)

    async def post(media_ids: list[str], steps: tuple[str, ...] = ("speciesnet", "animal_crop", "bioclip")):
        body = route.RunSelectedRequest(media_ids=media_ids, steps=[PipelineStepType(s) for s in steps])
        return await route.run_pipeline_selected(SimpleNamespace(state=SimpleNamespace(request_id="r")), body, SimpleNamespace(id="u1"))

    return post


async def test_run_ai_again_queues_one_job_for_the_photos_and_waits_for_the_batch_on_lambda(monkeypatch, api_jobs, run_selected):
    monkeypatch.setattr(settings, "WORKER_BACKEND", "lambda")
    monkeypatch.setattr(settings, "CPU_FUNCTION_NAME", "ww-dev-cpu")
    recorded: dict[str, dict] = {}

    async def set_job_dispatch(job_id, spec):
        recorded[job_id] = spec
        return True

    monkeypatch.setattr(store, "set_job_dispatch", set_job_dispatch)
    local = MagicMock()
    monkeypatch.setattr("app.jobs.runner.enqueue_local_job", local)

    response = await run_selected(["m1", "m2", "m3", "not-mine"])

    assert response.error is None
    assert response.data == {"job_id": "job0", "status": "queued", "deployment_ids": [DEP, DEP_B], "media_count": 3}
    assert api_jobs["job0"]["kind"] == AI_RUN_KIND and api_jobs["job0"]["deployment_ids"] == [DEP, DEP_B]
    spec = recorded["job0"]
    assert spec["name"] == "annotate_deployments_job" and spec["kind"] == "annotate"
    assert spec["args"] == [[DEP, DEP_B], "u1", False, ["m1", "m2", "m3"]] and spec["kwargs"] == {"pipeline": SELECTED}
    json.dumps(spec)  # api_jobs.job_data is jsonb
    local.assert_not_called()


async def test_run_ai_again_says_busy_while_an_upload_job_waits_on_one_of_the_deployments(monkeypatch, api_jobs, run_selected):
    api_jobs["upload"] = {"status": "queued", "kind": "ai_pipeline", "deployment_ids": [DEP_B]}
    enqueue = AsyncMock()
    monkeypatch.setattr(pipeline_run, "enqueue_job", enqueue)

    response = await run_selected(["m1", "m2"])

    assert response.error.code == "PIPELINE_BUSY" and response.error.retryable is True
    assert DEP_B in response.error.message
    assert list(api_jobs) == ["upload"] and enqueue.await_count == 0


async def test_run_ai_again_refuses_photos_the_caller_cannot_change(run_selected):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as refused:
        await run_selected(["not-mine"])
    assert refused.value.status_code == 403


async def test_run_ai_again_is_off_without_the_pipeline_flag(monkeypatch, api_jobs, run_selected):
    monkeypatch.setattr(settings, "FF_PIPELINE_ENABLED", False)
    response = await run_selected(["m1"])
    assert response.error.code == "FEATURE_DISABLED" and api_jobs == {}


def test_run_ai_again_takes_pipeline_steps_only():
    from pydantic import ValidationError

    from app.routers.media import RunSelectedRequest

    with pytest.raises(ValidationError):
        RunSelectedRequest(media_ids=["m1"], steps=["dinov3"])  # a picker choice that is not a pipeline step
    with pytest.raises(ValidationError):
        RunSelectedRequest(media_ids=["m1"], steps=[])


# ── the flag-built steps a run adds after the chosen models ──────────


@pytest.fixture
def flags_on(monkeypatch):
    """Every pipeline flag on, as on a server that runs the full upload pipeline."""
    for flag in (
        "FF_ML_ENABLED",
        "FF_PIPELINE_ENABLED",
        "FF_MEDIA_REGISTRY_ENABLED",
        "FF_GEMINI_PRESENCE_ENABLED",
        "FF_SPECIESNET_ENABLED",
        "FF_BIOCLIP_ENABLED",
        "FF_EVIDENCE_FUSION_ENABLED",
    ):
        monkeypatch.setattr(settings, flag, True)


async def test_run_ai_again_adds_the_flag_steps_after_the_chosen_model(monkeypatch, api_jobs, run_selected, flags_on):
    enqueue = AsyncMock()
    monkeypatch.setattr(pipeline_run, "enqueue_job", enqueue)

    await run_selected(["m1"], steps=("speciesnet", "animal_crop"))

    # Gemini and the consensus as an upload would run them; no thumbnails, and no BioCLIP: it was not ticked.
    steps = enqueue.await_args.kwargs["pipeline"]["steps"]  # pyright: ignore[reportOptionalMemberAccess]
    assert steps == ["speciesnet", "animal_crop", "gemini_presence", "evidence_fusion"]


async def test_run_ai_again_runs_only_the_chosen_model_with_those_flags_off(monkeypatch, api_jobs, run_selected, flags_on):
    monkeypatch.setattr(settings, "FF_GEMINI_PRESENCE_ENABLED", False)
    monkeypatch.setattr(settings, "FF_EVIDENCE_FUSION_ENABLED", False)
    enqueue = AsyncMock()
    monkeypatch.setattr(pipeline_run, "enqueue_job", enqueue)

    await run_selected(["m1"], steps=("bioclip",))

    assert enqueue.await_args.kwargs["pipeline"]["steps"] == ["bioclip"]  # pyright: ignore[reportOptionalMemberAccess]


async def test_on_lambda_the_dispatch_record_carries_the_flag_steps_to_the_batch(monkeypatch, api_jobs, run_selected, flags_on):
    monkeypatch.setattr(settings, "WORKER_BACKEND", "lambda")
    monkeypatch.setattr(settings, "CPU_FUNCTION_NAME", "ww-dev-cpu")
    recorded: dict[str, dict] = {}

    async def set_job_dispatch(job_id, spec):
        recorded[job_id] = spec
        return True

    monkeypatch.setattr(store, "set_job_dispatch", set_job_dispatch)
    monkeypatch.setattr("app.jobs.runner.enqueue_local_job", MagicMock())

    await run_selected(["m1", "m2"])
    spec = recorded["job0"]
    expected = ["speciesnet", "animal_crop", "bioclip", "gemini_presence", "evidence_fusion"]
    assert spec["kwargs"]["pipeline"]["steps"] == expected

    # The lease and ingest read the steps from that record, not from the flags.
    assert AnnotateRun.of_dispatch(spec).steps == [PipelineStepType(s) for s in expected]


async def test_a_deployment_run_gets_the_flag_steps_too(monkeypatch, api_jobs, flags_on):
    enqueue = AsyncMock()
    monkeypatch.setattr(pipeline_run, "enqueue_job", enqueue)

    await start_pipeline_run(PipelineRunRequest(deployment_id=DEP), "u1")

    assert enqueue.await_args.kwargs["pipeline"]["steps"] == ["speciesnet", "gemini_presence", "evidence_fusion"]  # pyright: ignore[reportOptionalMemberAccess]


def test_flag_steps_keep_fusion_last_and_add_nothing_to_an_empty_request(flags_on):
    assert pipeline_run.with_flag_steps(["evidence_fusion", "speciesnet"]) == ["speciesnet", "gemini_presence", "evidence_fusion"]
    assert pipeline_run.with_flag_steps(["media_prep"]) == ["media_prep", "gemini_presence", "evidence_fusion"]
    assert pipeline_run.with_flag_steps([]) == []


# ── the job ──────────────────────────────────────────────────────────


@pytest.fixture
def job_env(monkeypatch):
    """annotate_deployments_job with run_pipeline recorded and everything around it stubbed."""
    from app.domain import edge_reflection

    seen: list[dict] = []
    updates: list[dict] = []

    async def run(**kwargs):
        seen.append(kwargs)
        await kwargs["on_start"]()

    async def update_job(job_id, **kwargs):
        updates.append(kwargs)

    monkeypatch.setattr(definitions, "update_job", update_job)
    monkeypatch.setattr(definitions, "flush_pending_syncs", AsyncMock())
    monkeypatch.setattr(definitions, "build_pipeline_steps", lambda: [PipelineStepType.MEDIA_PREP, PipelineStepType.SPECIESNET])
    monkeypatch.setattr(edge_reflection, "reflect_edge_deployment", AsyncMock())
    monkeypatch.setattr(pipeline, "run_pipeline", run)
    monkeypatch.setattr(definitions, "emit_detection_notifications", AsyncMock())
    monkeypatch.setattr(definitions, "auto_embed_deployment", AsyncMock())
    return seen, updates


async def test_the_job_runs_the_requests_steps_and_settings(job_env):
    seen, updates = job_env
    await definitions.annotate_deployments_job(JOB, [DEP], "u1", pipeline=SPEC)

    (call,) = seen
    assert call["steps"] == [PipelineStepType.SPECIESNET, PipelineStepType.BIOCLIP]
    assert call["confidence_threshold"] == 0.6 and call["only_unannotated"] is False
    assert call["config"] == SPEC["config"] and call["config"] is not SPEC["config"]
    assert call["user_id"] == "u1" and call["force"] is False
    # The job waits behind a run already on the deployment instead of failing busy.
    assert "wait" not in call
    assert updates[-1]["status"] == JobStatus.COMPLETED


async def test_an_upload_job_keeps_the_flag_steps_and_the_defaults(job_env):
    seen, _updates = job_env
    await definitions.annotate_deployments_job(JOB, [DEP], "u1")

    (call,) = seen
    assert call["steps"] == [PipelineStepType.MEDIA_PREP, PipelineStepType.SPECIESNET]
    assert call["confidence_threshold"] == 0.2 and call["config"] == {} and call["only_unannotated"] is True


async def test_a_failed_run_ends_the_job_with_its_error(job_env, monkeypatch):
    _seen, updates = job_env

    async def broken(**_kwargs):
        raise RuntimeError("SpeciesNet weights missing")

    monkeypatch.setattr(pipeline, "run_pipeline", broken)
    await definitions.annotate_deployments_job(JOB, [DEP], "u1", pipeline=SPEC)

    final = updates[-1]
    assert final["status"] == JobStatus.COMPLETED_WITH_ERRORS and final["error"] == "SpeciesNet weights missing"


async def test_the_job_waits_for_a_run_already_on_the_deployment(job_env, monkeypatch):
    _seen, updates = job_env
    ran: list[str] = []

    async def held(deployment_id, *_args):
        ran.append(deployment_id)
        return PipelineRunResult(deployment_id=deployment_id)

    monkeypatch.setattr(pipeline, "run_pipeline", REAL_RUN_PIPELINE)
    monkeypatch.setattr(pipeline, "_run_pipeline_held", held)

    async with locks.exclusive(deployment_lock_name(DEP)):
        task = asyncio.create_task(definitions.annotate_deployments_job(JOB, [DEP], "u1", pipeline=SPEC))
        for _ in range(20):
            await asyncio.sleep(0)
        assert ran == [] and all(u.get("status") is None for u in updates)  # still queued
    await task

    assert ran == [DEP]
    assert updates[0]["status"] == JobStatus.PROCESSING and updates[-1]["status"] == JobStatus.COMPLETED


async def test_a_selected_run_reads_only_the_selected_photos_of_each_deployment(job_env):
    seen, updates = job_env
    await definitions.annotate_deployments_job(JOB, [DEP, DEP_B], "u1", False, ["m1", "m2"], pipeline=SELECTED)

    assert [(c["deployment_id"], c["media_ids"]) for c in seen] == [(DEP, ["m1", "m2"]), (DEP_B, ["m1", "m2"])]
    assert all(c["only_unannotated"] is False and c["force"] is False for c in seen)
    assert updates[-1]["status"] == JobStatus.COMPLETED


def test_each_deployment_gets_a_config_of_its_own():
    run = AnnotateRun.of(SPEC)
    first, second = run.run_kwargs(), run.run_kwargs()
    first["config"]["confidence_threshold"] = 0.9  # run_pipeline writes into the config it gets
    assert second["config"] == SPEC["config"] and run.config == SPEC["config"]


# ── the REANNZ batch ─────────────────────────────────────────────────


def _dispatch(pipeline_spec: dict | None = SPEC) -> dict:
    return {
        "backend": "batch",
        "name": "annotate_deployments_job",
        "kind": "annotate",
        "args": [[DEP], "u1"],
        "kwargs": {"pipeline": pipeline_spec} if pipeline_spec is not None else {},
    }


@pytest.fixture
def lease_env(monkeypatch):
    fetched: list[tuple[list, dict]] = []

    def fetch(svc, dep, steps, **kw):
        fetched.append((list(steps), kw))
        return [{"id": "m1", "deployment_id": dep, "file_path": "gdrive://abc"}]

    monkeypatch.setattr("app.domain.pipeline.fetch_pipeline_media", fetch)
    monkeypatch.setattr(batch_lease, "create_service_client", lambda: MagicMock())
    monkeypatch.setattr(settings, "FF_BIOCLIP_ENABLED", True)
    monkeypatch.setattr(settings, "BIOCLIP_RANK", "species")
    monkeypatch.setattr("app.jobs.definitions.build_pipeline_steps", lambda: [PipelineStepType.SPECIESNET])
    return fetched


async def test_the_lease_runs_the_models_with_the_jobs_settings(lease_env):
    work = await batch_lease._annotate_work(LEASE, _dispatch())

    assert work["models"] == {"speciesnet": True, "bioclip": True}
    config = work["config"]
    assert config["confidence_threshold"] == 0.6 and config["whole_frame_area"] == 0.5
    assert config["bioclip_labels"] == ["Rattus rattus"] and config["bioclip_rank"] == "species"
    ((steps, kw),) = lease_env
    assert steps == [PipelineStepType.SPECIESNET, PipelineStepType.BIOCLIP] and kw["only_unannotated"] is False


async def test_the_lease_sends_only_the_selected_photos(lease_env):
    dispatch = {**_dispatch(SELECTED), "args": [[DEP, DEP_B], "u1", False, ["m1", "m2", "m3"]]}

    work = await batch_lease._annotate_work(LEASE, dispatch)

    assert [kw["media_ids"] for _steps, kw in lease_env] == [["m1", "m2", "m3"], ["m1", "m2", "m3"]]
    assert all(kw["only_unannotated"] is False for _steps, kw in lease_env)
    assert len(work["items"]) == 2 and batch_lease._estimate_photos(dispatch) == 3


async def test_an_upload_lease_is_unchanged(lease_env):
    work = await batch_lease._annotate_work(LEASE, _dispatch(None))

    assert work["models"] == {"speciesnet": True, "bioclip": False}
    assert work["config"]["confidence_threshold"] == 0.2 and work["config"]["bioclip_labels"] is None
    ((steps, kw),) = lease_env
    assert steps == [PipelineStepType.SPECIESNET] and kw["only_unannotated"] is True


@pytest.mark.parametrize("flag_steps", [[], ["gemini_presence", "evidence_fusion"]])
async def test_ingest_writes_the_rows_with_the_jobs_settings(monkeypatch, flag_steps):
    from app.domain.batch_lease import part_key

    seen: list[dict] = []

    async def run_pipeline(**kwargs):
        seen.append(kwargs)

    monkeypatch.setattr("app.domain.pipeline.run_pipeline", run_pipeline)
    monkeypatch.setattr("app.domain.edge_reflection.reflect_edge_deployment", AsyncMock())
    monkeypatch.setattr(ingest, "refresh_job", AsyncMock(return_value=True))
    monkeypatch.setattr(ingest, "update_job", AsyncMock())
    # The flag-built steps the request resolved ride in the record (flags are off in this process).
    monkeypatch.setattr(ingest, "_dispatch_of", AsyncMock(return_value=_dispatch({**SPEC, "steps": SPEC["steps"] + flag_steps})))
    monkeypatch.setattr(ingest, "finalize_if_complete", AsyncMock(return_value=False))
    header = {"lease_id": LEASE, "job_id": JOB, "part": 1, "kind": "annotate"}
    await object_store.put_object(part_key(LEASE, JOB, 1), inference_cache.encode_part(header, [{"media_id": "m1", "deployment_id": DEP}]))

    assert await ingest.ingest_results_part(LEASE, JOB, 1) == "ingested"

    (call,) = seen
    assert call["steps"] == [PipelineStepType(s) for s in SPEC["steps"] + flag_steps] and call["media_ids"] == ["m1"]
    assert call["confidence_threshold"] == 0.6 and call["config"] == SPEC["config"] and call["only_unannotated"] is False
