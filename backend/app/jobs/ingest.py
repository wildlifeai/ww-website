# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ingest: turn the REANNZ cluster's results parts into rows, then finish the job.

One call per uploaded part (``ingest_results_part_job``, started by
``POST /api/worker/lease/{id}/part/complete``): the part is read from the object store,
checked against its lease, job and contract version, and for an annotate job
``run_pipeline`` runs on the part's photos with the precomputed providers of
``services/inference_cache.py`` active, so media prep, Gemini, SpeciesNet rows, crops,
BioCLIP rows and evidence fusion are written by the same code as the in-process path.

Idempotent per ``(lease, part)``: a ``.ingested`` marker is written after the part's rows.
:func:`finalize_if_complete` closes the job once the cluster's ``_done.json`` and every
part's marker exist: notifications and the follow-up clustering batch for an annotate job,
or, for an embed job, the job's own definition run on all its vectors at once (clustering
needs the whole deployment). A ``_finalized`` marker makes that once only.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import structlog

from app.domain.batch_lease import done_key, finalized_key, ingested_key, part_key
from app.jobs.store import create_job, job_heartbeat, refresh_job, update_job
from app.schemas.job import JobStatus
from app.services import inference_cache, object_store
from app.services.db_utils import rows_of
from app.services.supabase_client import create_service_client

logger = structlog.get_logger()


class IngestError(Exception):
    """A part is missing or does not belong to its lease and job."""


async def _dispatch_of(job_id: str) -> dict:
    """The job's dispatch record as Supabase holds it now."""
    import asyncio

    def _read() -> dict:
        rows = rows_of(create_service_client().table("api_jobs").select("job_data").eq("id", job_id).execute())
        return ((rows[0].get("job_data") or {}).get("dispatch") or {}) if rows else {}

    return await asyncio.to_thread(_read)


async def _load_part(lease_id: str, job_id: str, part: int) -> inference_cache.ResultsPart:
    found = await object_store.get_object(part_key(lease_id, job_id, part))
    if not found:
        raise IngestError(f"results part {part} of job {job_id} is not in the object store")
    return inference_cache.decode_part(found[0], lease_id=lease_id, job_id=job_id)


def _annotate_args(dispatch: dict) -> tuple[list[str], Optional[str], bool]:
    """``(deployment_ids, user_id, force)`` of an ``annotate_deployments_job`` record."""
    args, kwargs = dispatch.get("args") or [], dispatch.get("kwargs") or {}
    deployments = list(args[0] if args else kwargs.get("deployment_ids") or [])
    user_id = args[1] if len(args) > 1 else kwargs.get("user_id")
    force = bool(args[2] if len(args) > 2 else kwargs.get("force", False))
    return deployments, user_id, force


async def ingest_results_part(lease_id: str, job_id: str, part: int) -> str:
    """Write one part's rows. Returns ``ingested``, or ``skipped`` when it was done already."""
    if await object_store.object_exists(ingested_key(lease_id, job_id, part)):
        return "skipped"
    results = await _load_part(lease_id, job_id, part)
    await refresh_job(job_id)
    dispatch = await _dispatch_of(job_id)
    if results.header.get("kind") == "annotate":
        await _ingest_annotate(job_id, dispatch, results)
    marker = {"at": datetime.now(timezone.utc).isoformat(), "photos": len(results.media_ids), "misses": results.misses}
    await object_store.put_object(ingested_key(lease_id, job_id, part), json.dumps(marker).encode("utf-8"), "application/json")
    if any(results.misses.values()):
        logger.warning("batch_precomputed_misses", lease_id=lease_id, job_id=job_id, part=part, **results.misses)
    logger.info("batch_part_ingested", lease_id=lease_id, job_id=job_id, part=part, photos=len(results.media_ids))
    await finalize_if_complete(lease_id, job_id)
    return "ingested"


async def _ingest_annotate(job_id: str, dispatch: dict, results: inference_cache.ResultsPart) -> None:
    from app.domain.edge_reflection import reflect_edge_deployment
    from app.domain.pipeline import run_pipeline
    from app.jobs.pipeline_run import AnnotateRun

    _deployments, user_id, force = _annotate_args(dispatch)
    # The steps and settings the lease ran the models for (batch_lease._annotate_work).
    run = AnnotateRun.of_dispatch(dispatch)
    if not run.steps:
        return
    groups = results.by_deployment()
    with inference_cache.use(results):
        async with job_heartbeat(job_id):
            for n, (deployment_id, media_ids) in enumerate(groups.items()):
                # As auto_annotate_deployments: the camera's own scores first, best-effort.
                await reflect_edge_deployment(deployment_id)
                await run_pipeline(deployment_id=deployment_id, user_id=user_id, force=force, media_ids=media_ids, **run.run_kwargs())
                await update_job(job_id, message=f"Writing results, deployment {n + 1} of {len(groups)}")


async def finalize_if_complete(lease_id: str, job_id: str) -> bool:
    """Close the job if the cluster reported its parts and all are ingested. True when this call closed it."""
    from app.services.locks import exclusive

    async with exclusive(f"batch-finalize:{job_id}", ttl_seconds=900):
        if await object_store.object_exists(finalized_key(lease_id, job_id)):
            return False
        found = await object_store.get_object(done_key(lease_id, job_id))
        if not found:
            return False
        done = json.loads(found[0])
        total = int(done.get("parts_total") or 0)
        for part in range(1, total + 1):
            if not await object_store.object_exists(ingested_key(lease_id, job_id, part)):
                return False
        await refresh_job(job_id)
        dispatch = await _dispatch_of(job_id)
        if dispatch.get("kind") == "embed":
            await _finalize_embed(lease_id, job_id, dispatch, total)
        else:
            await _finalize_annotate(job_id, dispatch, list(done.get("failed_media") or []))
        await object_store.put_object(finalized_key(lease_id, job_id), b"{}", "application/json")
        logger.info("batch_job_finalized", lease_id=lease_id, job_id=job_id, parts=total)
        return True


async def _finalize_annotate(job_id: str, dispatch: dict, failed_media: list[str]) -> None:
    """Notifications per deployment, then the clustering for the new crops as its own batch job."""
    from app.config import settings
    from app.jobs.dispatch import enqueue_job
    from app.services.notifications_service import emit_detection_notifications

    deployments, user_id, _force = _annotate_args(dispatch)
    for deployment_id in deployments:
        try:
            await emit_detection_notifications(deployment_id)
        except Exception as exc:  # noqa: BLE001 - as auto_annotate: never fails the analysis
            logger.warning("batch_notifications_failed", deployment_id=deployment_id, error=str(exc))
    follow = ""
    if settings.FF_WILDLIFE_BRAIN_ENABLED and deployments:
        # DINOv3 embeds the hero crops ingest just stored, which the annotate lease could not know.
        embed_job = await create_job(
            user_id=user_id, kind="embedding", label=f"Clustering, {len(deployments)} deployment(s)", deployment_ids=deployments
        )
        await enqueue_job("embed_deployments_job", embed_job, deployments)
        follow = f"; clustering follows in job {embed_job[:8]}"
    unread = f", {len(failed_media)} photo(s) could not be read" if failed_media else ""
    await update_job(job_id, status=JobStatus.COMPLETED, progress=1.0, message=f"AI analysis complete{unread}{follow}")


async def _finalize_embed(lease_id: str, job_id: str, dispatch: dict, total: int) -> None:
    """Run the embed job's own definition with every vector the cluster computed for it."""
    from app.jobs import definitions

    merged: Optional[inference_cache.ResultsPart] = None
    for part in range(1, total + 1):
        loaded = await _load_part(lease_id, job_id, part)
        merged = loaded if merged is None else merged.merged_with(loaded)
    if merged is None:
        merged = inference_cache.ResultsPart(header={"lease_id": lease_id, "job_id": job_id, "kind": "embed"}, records=[])
    func = getattr(definitions, str(dispatch.get("name")))
    with inference_cache.use(merged):
        await func(job_id, *(dispatch.get("args") or []), **(dispatch.get("kwargs") or {}))
