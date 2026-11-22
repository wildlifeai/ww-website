# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The REANNZ batch: what is queued, leasing it to the cluster, and where its results go.

GPU jobs dispatched under ``WORKER_BACKEND=lambda`` wait in ``api_jobs`` with a
``job_data.dispatch`` record (``jobs/dispatch.py``). The cluster's cron asks
:func:`pending_summary`, its Slurm job takes work with :func:`claim`, reports with
:func:`heartbeat`, uploads results parts (:func:`part_target`, :func:`store_part`),
announces each (:func:`part_complete`, which starts ingest) and ends with :func:`finish`.

State lives where no stale copy can overwrite it:

- the lease is a ``services.locks`` lease row, renewed by heartbeats and released by
  :func:`finish`; an expired one is requeued by the reaper (``jobs/store.py``);
- each job's row gets ``status = processing`` and ``job_data.lease = {"id": ...}`` once, at claim;
- parts and their progress are objects under ``results/{lease}/{job}/`` in the object store:
  the part, a ``.ingested`` marker per part, ``_done.json`` from the cluster and
  ``_finalized`` once the job is complete (``jobs/ingest.py``).

The cluster never gets a database credential and never fetches a URL a user could have
written: originals in S3 come as presigned GETs, everything else through
``/api/worker/lease/{id}/media/{media_id}``, which resolves through the SSRF-guarded resolver.
"""

from __future__ import annotations

import asyncio
import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

import structlog

from app.config import settings
from app.jobs.store import batch_lease_name, patch_job_data
from app.schemas.job import JobStatus
from app.services import object_store
from app.services.db_utils import rows_of
from app.services.inference_cache import INFERENCE_CONTRACT
from app.services.supabase_client import create_service_client

if TYPE_CHECKING:
    from app.jobs.pipeline_run import AnnotateRun

logger = structlog.get_logger()

# Seconds of L4 time budgeted per photo for the Slurm --time (twice the measured 1.5 s).
SECONDS_PER_PHOTO_BUDGET = 3
PART_UPLOAD_TTL_SECONDS = 900
PART_CONTENT_TYPE = "application/gzip"


class LeaseError(Exception):
    """The lease is unknown or no longer held, or the job or media is not part of it."""


# ── keys in the object store ─────────────────────────────────────────


def part_key(lease_id: str, job_id: str, part: int) -> str:
    return f"{object_store.RESULTS_PREFIX}{lease_id}/{job_id}/part-{int(part):04d}.jsonl.gz"


def ingested_key(lease_id: str, job_id: str, part: int) -> str:
    return f"{object_store.RESULTS_PREFIX}{lease_id}/{job_id}/part-{int(part):04d}.ingested"


def done_key(lease_id: str, job_id: str) -> str:
    return f"{object_store.RESULTS_PREFIX}{lease_id}/{job_id}/_done.json"


def finalized_key(lease_id: str, job_id: str) -> str:
    return f"{object_store.RESULTS_PREFIX}{lease_id}/{job_id}/_finalized"


# ── reading the queue ────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _batch_rows(status: JobStatus) -> list[dict]:
    """``api_jobs`` rows of the batch in ``status``, oldest first. Blocking."""
    client = create_service_client()
    resp = (
        client.table("api_jobs")
        .select("id, status, job_data, created_at")
        .eq("status", status.value)
        .eq("job_data->dispatch->>backend", "batch")
        .order("created_at")
        .execute()
    )
    return rows_of(resp)


def _ready(row: dict, now: datetime) -> bool:
    """Past its debounce (``not_before``), so later upload chunks have joined it (#284)."""
    dispatch = (row.get("job_data") or {}).get("dispatch") or {}
    return str(dispatch.get("not_before") or "") <= now.isoformat()


def _job_deployments(dispatch: dict) -> list[str]:
    """The deployments a batch job covers, from its arguments."""
    args = dispatch.get("args") or []
    name = dispatch.get("name")
    if name == "annotate_deployments_job" or name == "embed_deployments_job":
        return [d for d in (args[0] if args else []) if d]
    return [args[0]] if args and args[0] else []


def _count_media(deployment_ids: list[str]) -> int:
    """Photos in the deployments, an upper bound on the work (some may be annotated already). Blocking."""
    if not deployment_ids:
        return 0
    resp = (
        create_service_client()
        .table("media")
        .select("id", count="exact", head=True)  # pyright: ignore[reportArgumentType]
        .in_("deployment_id", deployment_ids)
        .is_("deleted_at", "null")
        .execute()
    )
    return int(getattr(resp, "count", 0) or 0)


def _estimate_photos(dispatch: dict) -> int:
    kwargs = dispatch.get("kwargs") or {}
    args = dispatch.get("args") or []
    media_ids = kwargs.get("media_ids") or (args[3] if dispatch.get("name") == "annotate_deployments_job" and len(args) > 3 else None)
    if media_ids:
        return len(media_ids)
    return _count_media(_job_deployments(dispatch))


async def pending_summary() -> dict:
    """What the cron needs: whether to submit a Slurm job, which image, and for how long."""

    def _run() -> dict:
        now = _now()
        rows = [r for r in _batch_rows(JobStatus.QUEUED) if _ready(r, now)]
        photos = sum(_estimate_photos((r.get("job_data") or {}).get("dispatch") or {}) for r in rows)
        oldest = min((str((r.get("job_data") or {}).get("dispatch", {}).get("queued_at") or "") for r in rows), default="")
        oldest_minutes = (now - datetime.fromisoformat(oldest)).total_seconds() / 60 if oldest else 0.0
        submit = bool(rows) and (photos >= settings.BATCH_MIN_PHOTOS or oldest_minutes >= settings.BATCH_MAX_WAIT_MINUTES)
        minutes = 10 + math.ceil(photos * SECONDS_PER_PHOTO_BUDGET / 60)
        return {
            "jobs": len(rows),
            "photos": photos,
            "oldest_minutes": round(oldest_minutes, 1),
            "submit": submit,
            "image": settings.GPU_IMAGE,
            "time_limit_minutes": max(20, min(minutes, 6 * 60)),
            "contract": INFERENCE_CONTRACT,
        }

    return await asyncio.to_thread(_run)


# ── building a lease ─────────────────────────────────────────────────


def _media_item(lease_id: str, media: dict) -> dict:
    """Where the cluster fetches one original: a presigned GET for S3, else the API."""
    item = {"media_id": media["id"], "deployment_id": media.get("deployment_id")}
    path = str(media.get("file_path") or "")
    if path.startswith("s3://"):
        bucket, key = object_store.parse_s3_path(path)
        item["url"] = object_store.presign_get(key, settings.BATCH_LEASE_MINUTES * 60, bucket=bucket)
    else:
        item["path"] = f"/api/worker/lease/{lease_id}/media/{media['id']}"
    return item


def _public_rendition(url: str) -> bool:
    return url.startswith(f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1/object/public/")


def _crop_item(lease_id: str, crop: dict) -> dict:
    item = {"media_id": crop["id"], "deployment_id": crop.get("deployment_id")}
    if _public_rendition(str(crop.get("crop_url") or "")):
        item["url"] = crop["crop_url"]
    else:
        item["path"] = f"/api/worker/lease/{lease_id}/media/{crop['id']}?crop=1"
    return item


def _annotate_config(run: AnnotateRun) -> dict:
    """The settings the cluster's models need, as ``run_pipeline`` will use them for ``run`` at ingest."""
    from app.domain.pipeline import DetectionCutoffs

    config: dict[str, Any] = {"confidence_threshold": run.confidence_threshold}
    config.update(DetectionCutoffs.from_config(run.config).as_config())
    config["per_detection"] = settings.FF_PER_CROP_CLASSIFY_ENABLED
    config["bioclip_rank"] = run.config.get("bioclip_rank", settings.BIOCLIP_RANK)
    config["bioclip_labels"] = run.config.get("bioclip_labels")
    return config


async def _annotate_work(lease_id: str, dispatch: dict) -> Optional[dict]:
    """The photos and models of one annotate job, or None when the pipeline has nothing to run."""
    from app.domain.pipeline import fetch_pipeline_media
    from app.jobs.pipeline_run import AnnotateRun
    from app.schemas.pipeline import PipelineStepType

    # The job's own steps and settings (POST /api/pipeline/run), or the flags' for an upload.
    run = AnnotateRun.of_dispatch(dispatch)
    steps = run.steps
    speciesnet = PipelineStepType.SPECIESNET in steps
    bioclip = PipelineStepType.BIOCLIP in steps and settings.FF_BIOCLIP_ENABLED
    if not steps:
        return None
    args = dispatch.get("args") or []
    kwargs = dispatch.get("kwargs") or {}
    force = bool(kwargs.get("force", args[2] if len(args) > 2 else False))
    media_ids = kwargs.get("media_ids", args[3] if len(args) > 3 else None)
    svc = create_service_client()
    items: list[dict] = []
    for dep in _job_deployments(dispatch):
        media = await asyncio.to_thread(
            fetch_pipeline_media, svc, dep, steps, only_unannotated=run.only_unannotated, force=force, media_ids=media_ids
        )
        items.extend(_media_item(lease_id, m) for m in media)
    return {"models": {"speciesnet": speciesnet, "bioclip": bioclip}, "config": _annotate_config(run), "items": items}


async def _embed_work(lease_id: str, dispatch: dict) -> dict:
    """The hero crops of one embed job: DINOv3 embeds the same bytes the website stored."""
    from app.domain.wildlife_brain import _fetch_crops

    args = dispatch.get("args") or []
    model = (dispatch.get("kwargs") or {}).get("model_name") or (args[1] if len(args) > 1 else None) or settings.EMBEDDING_DEFAULT_MODEL
    items: list[dict] = []
    for dep in _job_deployments(dispatch):
        items.extend(_crop_item(lease_id, c) for c in await _fetch_crops(dep))
    return {"models": {"dinov3": model}, "config": {}, "items": items}


async def claim(max_photos: Optional[int] = None, slurm_job_id: str = "") -> Optional[dict]:
    """Lease queued batch jobs to the cluster, oldest first, up to ``max_photos`` photos.

    Returns the manifest, or None when nothing is ready. A job always goes whole into one
    lease (parts split it), so a lease may run over ``max_photos`` by one job. A job with
    nothing left to run is completed on the spot.
    """
    from app.services.locks import acquire_lease, exclusive, release_lease

    limit = max_photos or settings.BATCH_LEASE_MAX_PHOTOS
    lease_id = str(uuid.uuid4())
    name = batch_lease_name(lease_id)
    ttl = settings.BATCH_LEASE_MINUTES * 60
    async with exclusive("batch-claim", ttl_seconds=120):
        now = _now()
        rows = [r for r in await asyncio.to_thread(_batch_rows, JobStatus.QUEUED) if _ready(r, now)]
        if not rows:
            return None
        if not await asyncio.to_thread(acquire_lease, name, lease_id, ttl):
            raise LeaseError("could not take a new lease row")
        jobs: list[dict] = []
        total = 0
        for row in rows:
            if total >= limit:
                break
            dispatch = (row.get("job_data") or {}).get("dispatch") or {}
            try:
                work = await (_embed_work(lease_id, dispatch) if dispatch.get("kind") == "embed" else _annotate_work(lease_id, dispatch))
            except Exception as exc:  # noqa: BLE001 - one bad job must not block the queue
                logger.error("batch_claim_job_failed", job_id=row["id"], error=str(exc))
                await patch_job_data(row["id"], {"error": f"Could not prepare the GPU batch: {exc}"}, status=JobStatus.FAILED)
                continue
            if not work or not work["items"]:
                await _complete_empty(row["id"], dispatch)
                continue
            await patch_job_data(
                row["id"],
                {
                    "lease": {"id": lease_id, "leased_at": now.isoformat(), "slurm_job_id": slurm_job_id},
                    "message": f"Running on the GPU cluster ({len(work['items'])} photos)",
                    "progress": 0.05,
                },
                status=JobStatus.PROCESSING,
            )
            jobs.append({"job_id": row["id"], "kind": dispatch.get("kind"), **work})
            total += len(work["items"])
        if not jobs:
            await asyncio.to_thread(release_lease, name, lease_id)
            return None
    logger.info("batch_leased", lease_id=lease_id, jobs=len(jobs), photos=total, slurm_job_id=slurm_job_id)
    return {
        "lease_id": lease_id,
        "contract": INFERENCE_CONTRACT,
        "expires_at": (now + timedelta(seconds=ttl)).isoformat(),
        "heartbeat_seconds": max(60, ttl // 6),
        "part_max_photos": settings.BATCH_PART_MAX_PHOTOS,
        "jobs": jobs,
    }


async def _complete_empty(job_id: str, dispatch: dict) -> None:
    """A batch job with no photos left: close it as its in-process run would have."""
    message = "No new photos to analyse" if dispatch.get("kind") == "annotate" else "No crops to embed"
    await patch_job_data(job_id, {"progress": 1.0, "message": message}, status=JobStatus.COMPLETED)


# ── a live lease ─────────────────────────────────────────────────────


def _lease_rows(lease_id: str) -> list[dict]:
    resp = (
        create_service_client()
        .table("api_jobs")
        .select("id, status, job_data")
        .eq("status", JobStatus.PROCESSING.value)
        .eq("job_data->lease->>id", lease_id)
        .execute()
    )
    return rows_of(resp)


async def _lease_jobs(lease_id: str) -> list[dict]:
    rows = await asyncio.to_thread(_lease_rows, lease_id)
    if not rows:
        raise LeaseError(f"unknown lease {lease_id}")
    return rows


async def _require_job(lease_id: str, job_id: str) -> dict:
    for row in await _lease_jobs(lease_id):
        if row["id"] == job_id:
            return row
    raise LeaseError(f"job {job_id} is not in lease {lease_id}")


async def heartbeat(lease_id: str, done: int = 0, total: int = 0) -> dict:
    """Renew the lease and show the cluster's progress on its jobs. ``cancel`` tells the job to stop."""
    from app.services.locks import renew_lease

    ttl = settings.BATCH_LEASE_MINUTES * 60
    if not await asyncio.to_thread(renew_lease, batch_lease_name(lease_id), lease_id, ttl):
        return {"cancel": True}
    progress = 0.05 + 0.6 * (done / total) if total else 0.05
    for row in await asyncio.to_thread(_lease_rows, lease_id):
        await patch_job_data(row["id"], {"progress": round(min(progress, 0.65), 3), "message": f"GPU cluster: {done} of {total} photos"})
    return {"cancel": False, "expires_at": (_now() + timedelta(seconds=ttl)).isoformat()}


async def part_target(lease_id: str, job_id: str, part: int) -> dict:
    """Where the cluster PUTs one results part: S3 directly, or the API on other backends."""
    await _require_job(lease_id, job_id)
    headers = {"Content-Type": PART_CONTENT_TYPE}
    if settings.OBJECT_STORE_BACKEND == "s3":
        return {
            "method": "PUT",
            "url": object_store.presign_put(part_key(lease_id, job_id, part), PART_UPLOAD_TTL_SECONDS, PART_CONTENT_TYPE),
            "headers": headers,
        }
    return {"method": "PUT", "path": f"/api/worker/lease/{lease_id}/part/{job_id}/{int(part)}", "headers": headers}


async def store_part(lease_id: str, job_id: str, part: int, data: bytes) -> None:
    """The API-side upload of a part, for the azure and local backends."""
    await _require_job(lease_id, job_id)
    await object_store.put_object(part_key(lease_id, job_id, part), data, PART_CONTENT_TYPE)


async def part_complete(lease_id: str, job_id: str, part: int) -> None:
    """A part is uploaded: start its ingest (``jobs/ingest.py``)."""
    from app.jobs.dispatch import enqueue_job

    await _require_job(lease_id, job_id)
    if not await object_store.object_exists(part_key(lease_id, job_id, part)):
        raise LeaseError(f"part {part} of job {job_id} was not uploaded")
    await enqueue_job("ingest_results_part_job", lease_id, job_id, int(part))


async def finish(lease_id: str, reports: list[dict]) -> None:
    """The cluster is done with the lease: record each job's part count, free the lease, finalise.

    ``reports`` is ``[{"job_id", "parts_total", "failed_media": [...]}]``. A job whose parts
    are all ingested already is finalised now; otherwise the last ingest finalises it.
    """
    from app.jobs.dispatch import enqueue_job
    from app.services.locks import release_lease

    rows = {r["id"] for r in await _lease_jobs(lease_id)}
    for report in reports:
        job_id = str(report.get("job_id"))
        if job_id not in rows:
            raise LeaseError(f"job {job_id} is not in lease {lease_id}")
        body = {"parts_total": int(report.get("parts_total") or 0), "failed_media": list(report.get("failed_media") or [])}
        await object_store.put_object(done_key(lease_id, job_id), json.dumps(body).encode("utf-8"), "application/json")
    await asyncio.to_thread(release_lease, batch_lease_name(lease_id), lease_id)
    for report in reports:
        await enqueue_job("finalize_batch_job", lease_id, str(report.get("job_id")))
    logger.info("batch_lease_finished", lease_id=lease_id, jobs=len(reports))


async def media_bytes(lease_id: str, media_id: str, crop: bool = False) -> Optional[tuple[bytes, str]]:
    """One original (or hero crop) of a leased job, through the SSRF-guarded resolver."""
    from app.domain.media_resolver import resolve_media

    deployments: set[str] = set()
    for row in await _lease_jobs(lease_id):
        deployments.update(_job_deployments((row.get("job_data") or {}).get("dispatch") or {}))

    def _media() -> Optional[dict]:
        resp = (
            create_service_client().table("media").select("id, deployment_id, file_path, media_assets(animal_crop_url)").eq("id", media_id).execute()
        )
        found = rows_of(resp)
        return found[0] if found else None

    media = await asyncio.to_thread(_media)
    if not media or media.get("deployment_id") not in deployments:
        raise LeaseError(f"media {media_id} is not in lease {lease_id}")
    source = media.get("file_path")
    if crop:
        assets = media.get("media_assets")
        if isinstance(assets, list):
            assets = assets[0] if assets else None
        source = (assets or {}).get("animal_crop_url")
    return await resolve_media(str(source or ""), size="full") if source else None
