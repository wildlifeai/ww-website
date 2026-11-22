# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""AI runs people ask for, as jobs (#452): ``POST /api/pipeline/run`` on a deployment and the
grid's *Run AI again* (``POST /api/media/run-selected``) on selected photos.

Both answer at once with an ``ai_run`` job, which ``annotate_deployments_job`` runs with the
request's own steps and settings (its ``pipeline`` keyword) and, for selected photos, its
``media_ids``: the ARQ worker on Azure, this process locally, and under
``WORKER_BACKEND=lambda`` the REANNZ batch, whose lease (``domain/batch_lease.py``) and ingest
(``jobs/ingest.py``) read the same settings from the job's dispatch record. An annotate job
without ``pipeline`` runs what the feature flags build, as an upload's does.

After the steps a request picks, a run adds the other steps an upload's run builds from the
feature flags (Gemini presence, evidence fusion, never media prep), so a re-run photo ends with
the rows an uploaded one has. They are resolved here and written into ``pipeline``, so the
dispatch record carries them to the batch.

``run_pipeline``'s per-deployment lock still serialises the runs. On top of it a request is
refused while any of its deployments has an AI job queued or running, checked and created under
the lock the upload's coalescing takes, so two clicks make one job. The kind is not
``ai_pipeline``, so an upload never joins a run whose steps are not its own (#284).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

from app.domain.pipeline import PipelineBusyError, get_step
from app.jobs.dispatch import enqueue_job
from app.jobs.store import create_job, find_active_ai_jobs, flush_pending_syncs, update_job
from app.schemas.job import JobStatus
from app.schemas.pipeline import PipelineRunRequest, PipelineStepType
from app.services.db_utils import rows_of
from app.services.locks import exclusive
from app.services.supabase_client import create_service_client

logger = structlog.get_logger()

AI_RUN_KIND = "ai_run"

# The steps a request picks itself. The flags never add one of these to a request's run, so
# media prep (thumbnails) stays out and a model the person left unticked does not run.
PICKED_STEPS = frozenset({PipelineStepType.MEDIA_PREP, PipelineStepType.SPECIESNET, PipelineStepType.ANIMAL_CROP, PipelineStepType.BIOCLIP})


@dataclass(frozen=True)
class AnnotateRun:
    """The steps and settings an annotate job runs: a request's own, or an upload's from the flags."""

    steps: list[PipelineStepType]
    confidence_threshold: float = 0.2
    config: dict[str, Any] = field(default_factory=dict)
    only_unannotated: bool = True

    @classmethod
    def of(cls, pipeline: Optional[dict]) -> AnnotateRun:
        """From an annotate job's ``pipeline`` keyword; None is an upload's run."""
        if pipeline is None:
            from app.jobs.definitions import build_pipeline_steps

            return cls(steps=build_pipeline_steps())
        return cls(
            steps=[PipelineStepType(s) for s in pipeline.get("steps") or []],
            confidence_threshold=float(pipeline.get("confidence_threshold", 0.2)),
            config=dict(pipeline.get("config") or {}),
            only_unannotated=bool(pipeline.get("only_unannotated", True)),
        )

    @classmethod
    def of_dispatch(cls, dispatch: dict) -> AnnotateRun:
        """From an ``annotate_deployments_job`` dispatch record, as the REANNZ batch holds it."""
        return cls.of((dispatch.get("kwargs") or {}).get("pipeline"))

    def run_kwargs(self) -> dict[str, Any]:
        """``run_pipeline``'s keywords for one deployment, with a config of its own: the run adds to it."""
        return {
            "steps": list(self.steps),
            "confidence_threshold": self.confidence_threshold,
            "config": dict(self.config),
            "only_unannotated": self.only_unannotated,
        }


def with_flag_steps(steps: list[str]) -> list[str]:
    """``steps``, then the other steps an upload's run would add from the flags; evidence fusion last.

    Nothing is added to an empty request. Evidence fusion reads what every other step wrote, so
    it goes last however the request ordered it.
    """
    from app.jobs.definitions import build_pipeline_steps

    chosen = [PipelineStepType(s) for s in steps]
    if not chosen:
        return []
    ordered = [*chosen, *(s for s in build_pipeline_steps() if s not in PICKED_STEPS and s not in chosen)]
    fusion = PipelineStepType.EVIDENCE_FUSION
    if fusion in ordered:
        ordered = [s for s in ordered if s != fusion] + [fusion]
    return [s.value for s in ordered]


async def start_ai_run(deployment_ids: list[str], user_id: str, pipeline: dict, label: str, media_ids: Optional[list[str]] = None) -> str:
    """Queue ``pipeline`` on the deployments (only ``media_ids`` of them when given) as an ``ai_run`` job.

    The flag-built steps are added to ``pipeline``'s (:func:`with_flag_steps`). Raises
    ``ValueError`` for a step with no implementation, and ``PipelineBusyError`` for the first
    deployment that already has an AI job queued or running.
    """
    pipeline = {**pipeline, "steps": with_flag_steps(list(pipeline.get("steps") or []))}
    for step in pipeline["steps"]:
        get_step(PipelineStepType(step))
    async with exclusive("ai-coalesce", ttl_seconds=60):
        active = [*await find_active_ai_jobs(), *await find_active_ai_jobs(AI_RUN_KIND)]
        held = {d for job in active for d in job.get("deployment_ids") or []}
        busy = next((d for d in deployment_ids if d in held), None)
        if busy:
            raise PipelineBusyError(busy)
        job_id = await create_job(user_id=user_id, kind=AI_RUN_KIND, label=label, deployment_ids=deployment_ids)
        # In api_jobs before the lock goes: the next request must see it, and the batch's
        # dispatch record is written onto this row.
        await flush_pending_syncs(job_id)
    try:
        # Positional as the upload's and the CamtrapDP import's: the batch lease reads media_ids from args[3].
        await enqueue_job("annotate_deployments_job", job_id, deployment_ids, user_id, False, media_ids, pipeline=pipeline)
    except Exception as exc:
        # A row left queued would keep the deployments busy until the reaper fails it.
        await update_job(job_id, status=JobStatus.FAILED, error=f"Could not queue the AI run: {exc}")
        raise
    logger.info("ai_run_queued", job_id=job_id, deployments=len(deployment_ids), photos=len(media_ids or []), steps=pipeline.get("steps"))
    return job_id


async def start_pipeline_run(body: PipelineRunRequest, user_id: str) -> str:
    """``POST /api/pipeline/run``: the request's run on its deployment."""
    pipeline = body.model_dump(mode="json", exclude={"deployment_id"})
    return await start_ai_run([body.deployment_id], user_id, pipeline, label=f"Run AI on deployment {body.deployment_id[:8]}")


def _deployments_of(media_ids: list[str]) -> list[str]:
    """The deployments of the photos, in the order the photos first name them. Blocking (PostgREST)."""
    svc = create_service_client()
    of: dict[str, str] = {}
    for start in range(0, len(media_ids), 200):  # keep the in.(...) list inside a URL
        for row in rows_of(svc.table("media").select("id, deployment_id").in_("id", media_ids[start : start + 200]).execute()):
            if row.get("deployment_id"):
                of[row["id"]] = row["deployment_id"]
    return list(dict.fromkeys(of[m] for m in media_ids if m in of))


async def start_selected_run(media_ids: list[str], user_id: str, steps: list[PipelineStepType], confidence_threshold: float) -> tuple[str, list[str]]:
    """The grid's *Run AI again*: ``steps`` on the selected photos. Returns the job and its deployments.

    ``only_unannotated`` is off so a chosen model also runs on photos another model has
    labelled; run_pipeline still skips the photos this run's models have done and every photo
    with a human verdict.
    """
    deployment_ids = await asyncio.to_thread(_deployments_of, media_ids)
    if not deployment_ids:
        raise ValueError("None of the selected photos were found.")
    pipeline = {"steps": [s.value for s in steps], "confidence_threshold": confidence_threshold, "config": {}, "only_unannotated": False}
    job_id = await start_ai_run(deployment_ids, user_id, pipeline, label=f"Run AI on {len(media_ids)} photo(s)", media_ids=media_ids)
    return job_id, deployment_ids
