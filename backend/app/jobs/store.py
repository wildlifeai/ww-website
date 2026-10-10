# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Job status store — Redis-backed with in-memory fallback.

Every job has a key ``job:{id}`` in Redis containing its current status,
progress, summary, and configuration.  TTL is 24 hours.

Events are stored in a **separate** Redis list ``job:{id}:events`` to avoid
bloating the main job key (important for large batches with many events).
Each event carries a monotonic ``seq`` number so the frontend can safely
consume events even when the list is trimmed.

When Redis is unavailable (e.g. local dev), falls back to simple
in-memory dicts so endpoints don't crash.
"""

import asyncio
import contextlib
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

import structlog

from app.schemas.job import (
    EventType,
    JobInfo,
    JobStatus,
    ProgressEvent,
    ProgressPhase,
)
from app.services.db_utils import rows_of
from app.services.supabase_client import create_service_client

logger = structlog.get_logger()

# In-memory stores (Primary fast path)
_memory_store: Dict[str, str] = {}
_memory_events: Dict[str, List[str]] = {}

# Per-job locks to serialise summary updates
_summary_locks: Dict[str, asyncio.Lock] = {}

# Strong references to in-flight detached Supabase syncs, keyed by job_id. asyncio
# holds only a WEAK reference to a bare create_task(), so a fire-and-forget sync
# could be garbage-collected before it ran — which is how the ARQ worker left
# completed jobs stuck at 'processing' (and pinned the GPU awake). Keep a reference
# until the task finishes. Keying by job_id lets a terminal write drain only ITS
# own syncs (flush_pending_syncs(job_id)) so a slow sync on another job can't block
# it; the worker teardown drains all (flush_pending_syncs()).
_pending_sync_tasks: dict[str, set[asyncio.Task]] = {}

# Statuses after which no further work happens — their sync must be durable.
_TERMINAL_STATUSES = frozenset({JobStatus.COMPLETED.value, JobStatus.COMPLETED_WITH_ERRORS.value, JobStatus.FAILED.value})

# How often job_heartbeat() refreshes api_jobs.updated_at during a long pipeline step.
# Must stay well under the shortest consumer window: the KEDA scaler's query
# (updated_at > now() - interval '15 minutes' on dev) and the 60-min stale-job reaper.
_HEARTBEAT_INTERVAL_SECONDS = 60

MAX_EVENTS_RETURNED = 50


def _hydrate_from_supabase(job_id: str) -> Optional[str]:
    """Load a job from Supabase into this process's memory store; returns the raw JSON.

    The memory store is **per-process**: jobs are created on the API process, but ARQ jobs
    (e.g. the offloaded AI pipeline) run on the worker process. Without hydration, the
    worker's ``update_job``/``emit_event`` calls found nothing in memory and silently
    no-opped — so AI jobs stayed 'queued' in ``api_jobs`` forever and the frontend's
    "Processing" banner never cleared. Mirrors the lazy load in :func:`get_job`.
    """
    try:
        client = create_service_client()
        resp = client.table("api_jobs").select("job_data").eq("id", job_id).execute()
        if resp.data:
            db_data = rows_of(resp)[0]["job_data"] or {}
            events = db_data.pop("events", [])
            _memory_events[f"job:{job_id}:events"] = [json.dumps(e) for e in events]
            raw = json.dumps(db_data)
            _memory_store[f"job:{job_id}"] = raw
            return raw
    except Exception as e:
        logger.debug("job_hydrate_failed", job_id=job_id, error=str(e))
    return None


async def _get_raw(job_id: str) -> Optional[str]:
    """Raw job JSON from memory, hydrating from Supabase on a cross-process miss."""
    raw = _memory_store.get(f"job:{job_id}")
    if raw:
        return raw
    return await asyncio.to_thread(_hydrate_from_supabase, job_id)


def _refresh_if_newer(job_id: str, local_updated_at: Optional[str]) -> Optional[str]:
    """Re-hydrate a job from Supabase only if the DB copy is newer than memory.

    The complement of :func:`_hydrate_from_supabase` for polling: a job created on the API
    but *executed on the worker* leaves the API's memory copy frozen at 'queued' — polls
    (``get_job``) must pick up the worker's Supabase writes or the upload dock never sees the
    AI job finish. Jobs running in *this* process always have memory >= DB (memory is written
    first, then synced), so the strict ISO-timestamp comparison never clobbers in-flight state.
    """
    try:
        client = create_service_client()
        resp = client.table("api_jobs").select("job_data").eq("id", job_id).execute()
        if not resp.data:
            return None
        db_data = rows_of(resp)[0]["job_data"] or {}
        db_updated = db_data.get("updated_at") or ""
        if local_updated_at and db_updated <= local_updated_at:
            return None  # memory is current (or newer — this process owns the job)
        events = db_data.pop("events", [])
        _memory_events[f"job:{job_id}:events"] = [json.dumps(e) for e in events]
        raw = json.dumps(db_data)
        _memory_store[f"job:{job_id}"] = raw
        return raw
    except Exception as e:
        logger.debug("job_refresh_failed", job_id=job_id, error=str(e))
        return None


async def _sync_to_supabase(job_id: str, *, flush: bool = False) -> None:
    """Synchronize local memory state to Supabase.

    ``flush=True`` **awaits** the write so it's durable before returning — used for
    terminal status writes: a detached task can be dropped when the coroutine
    returns and the event loop settles (exactly what stranded ARQ-worker jobs at
    'processing'). Otherwise the write is detached for speed, but tracked in
    ``_pending_sync_tasks`` so it isn't garbage-collected mid-flight.
    """
    key = f"job:{job_id}"
    if key not in _memory_store:
        return

    def _run_sync():
        # Snapshot the LATEST memory state at *write* time, not when this sync was
        # scheduled. A detached sync queued mid-run (status 'processing') would
        # otherwise carry that stale snapshot and, if it lands after the awaited
        # terminal write, clobber 'completed' back to 'processing' — exactly what
        # stranded ARQ jobs and kept the GPU billing. Reading here means every sync
        # (however late) writes the current state.
        raw = _memory_store.get(key)
        if not raw:
            return
        data_json = json.loads(raw)
        # Snapshot the events list before iterating: emit_event may append to the
        # live list from the event-loop thread while this runs in the thread pool.
        # ``list(...)`` is a single GIL-held copy (atomic); the Python-level
        # comprehension then iterates our private copy.
        events_raw = list(_memory_events.get(f"{key}:events", []))
        data_json["events"] = [json.loads(e) for e in events_raw]
        try:
            client = create_service_client()
            status_val = data_json.get("status", "queued")
            client.table("api_jobs").upsert({"id": job_id, "status": status_val, "job_data": data_json}).execute()
        except Exception as e:
            logger.debug("supabase_sync_skipped", error=str(e))

    if flush:
        await asyncio.to_thread(_run_sync)
        return

    task = asyncio.create_task(asyncio.to_thread(_run_sync))
    _pending_sync_tasks.setdefault(job_id, set()).add(task)

    def _discard(t: asyncio.Task) -> None:
        tasks = _pending_sync_tasks.get(job_id)
        if tasks is not None:
            tasks.discard(t)
            if not tasks:
                _pending_sync_tasks.pop(job_id, None)

    task.add_done_callback(_discard)


async def flush_pending_syncs(job_id: Optional[str] = None) -> None:
    """Await in-flight detached Supabase syncs.

    With ``job_id``, drains only that job's syncs — used before a terminal write so
    a slow/hung sync on a DIFFERENT job can't block it. Without it, drains every
    job's syncs: the ARQ adapter's ``finally`` teardown net, so a job's last writes
    reach ``api_jobs`` before the worker goes idle / scales to zero.
    """
    if job_id is not None:
        pending = [t for t in _pending_sync_tasks.get(job_id, set()) if not t.done()]
    else:
        pending = [t for tasks in _pending_sync_tasks.values() for t in tasks if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def reap_stale_jobs(max_age_minutes: int = 60) -> int:
    """Fail queued/processing jobs whose last update is older than ``max_age_minutes``.

    Replaces the old ``recover_stuck_jobs`` (which failed EVERY 'processing' job at API
    startup — wrong now that AI jobs legitimately run on the separate worker process across
    API restarts). A job orphaned by a lost enqueue, a worker crash, or a pre-hydration-fix
    silent no-op otherwise sits 'queued'/'processing' in ``api_jobs`` forever — keeping the
    Annotations "Processing" banner up for days. Runs periodically from the API lifespan.

    The threshold is deliberately generous: active jobs refresh ``updated_at`` on every
    progress/status write (at minimum once per deployment for AI jobs), so anything silent
    for an hour is genuinely dead. Returns the number of jobs reaped.
    """
    from datetime import timedelta  # noqa: PLC0415

    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)).isoformat()

    def _run() -> int:
        client = create_service_client()
        resp = (
            client.table("api_jobs").select("id, job_data, updated_at").in_("status", [JobStatus.QUEUED.value, JobStatus.PROCESSING.value]).execute()
        )
        reaped = 0
        for row in rows_of(resp):
            jd = row.get("job_data") or {}
            last = jd.get("updated_at") or row.get("updated_at") or ""
            if not last or last >= cutoff:
                continue
            jd["status"] = JobStatus.FAILED.value
            jd["error"] = f"Stalled: no progress for over {max_age_minutes} minutes."
            jd["message"] = "❌ Failed: the job stalled and was cleaned up automatically."
            jd["updated_at"] = datetime.now(timezone.utc).isoformat()
            client.table("api_jobs").update({"status": jd["status"], "job_data": jd}).eq("id", row["id"]).execute()
            # Keep this process's memory copy consistent so polls don't resurrect it.
            _memory_store[f"job:{row['id']}"] = json.dumps(jd)
            reaped += 1
            logger.warning("stale_job_reaped", job_id=row["id"], last_update=last, kind=jd.get("kind"))
        return reaped

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        logger.warning("reap_stale_jobs_failed", error=str(e))
        return 0


async def create_job(
    user_id: Optional[str] = None,
    kind: Optional[str] = None,
    label: Optional[str] = None,
    deployment_ids: Optional[List[str]] = None,
    organisation_id: Optional[str] = None,
) -> str:
    """Create a new job entry locally and sync to Supabase.

    ``user_id`` is stamped into ``job_data`` so the owner can list their own jobs
    (`api_jobs` has no owner column, see :func:`list_jobs`). ``organisation_id`` does the
    same for a job an organisation's API key started: only that organisation's keys may
    read it (``GET /api/v1/jobs/{id}``). ``kind`` is a coarse
    category ('upload', 'ai_pipeline', 'export', …) and ``label`` a human summary,
    both surfaced in the processing-history view. ``deployment_ids`` records which
    deployments the job touches so the Annotations grid can show a "being processed"
    banner for the deployments in view (see :func:`set_job_deployments` for jobs
    whose deployments are only known mid-run, like uploads).
    """
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    job_data = {
        "job_id": job_id,
        "status": JobStatus.QUEUED.value,
        "progress": 0.0,
        "created_at": now,
        "updated_at": now,
        "result_url": None,
        "error": None,
        "message": None,
        "current_phase": None,
        "summary": None,
        "user_id": user_id,
        "organisation_id": organisation_id,
        "kind": kind,
        "label": label,
        "deployment_ids": deployment_ids or [],
        "_next_seq": 0,
    }

    _memory_store[f"job:{job_id}"] = json.dumps(job_data)
    await _sync_to_supabase(job_id)
    return job_id


async def set_job_deployments(job_id: str, deployment_ids: List[str]) -> None:
    """Record the deployments a job touches once they're known (best-effort).

    Uploads resolve their deployments mid-run (EXIF / folder binding after the file
    buffer), so they call this from the AI phase rather than at :func:`create_job`.
    """
    raw = await _get_raw(job_id)
    if not raw:
        return
    data = json.loads(raw)
    merged = sorted({*(data.get("deployment_ids") or []), *deployment_ids})
    data["deployment_ids"] = merged
    _memory_store[f"job:{job_id}"] = json.dumps(data)
    await _sync_to_supabase(job_id)


async def list_jobs(user_id: str, limit: int = 50) -> List[dict]:
    """Return summaries of a user's recent jobs (newest first), from Supabase.

    Scoped by ``job_data->>user_id`` because ``api_jobs`` has no owner column.
    Returns light summaries (no event lists) for the processing-history view;
    callers fetch full events per job via :func:`get_job`.
    """

    def _run() -> List[dict]:
        client = create_service_client()
        resp = (
            client.table("api_jobs")
            .select("id, status, job_data, created_at, updated_at")
            .eq("job_data->>user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        out: List[dict] = []
        for row in rows_of(resp):
            jd = row.get("job_data") or {}
            out.append(
                {
                    "job_id": row["id"],
                    "status": row.get("status"),
                    "kind": jd.get("kind"),
                    "label": jd.get("label"),
                    "deployment_ids": jd.get("deployment_ids") or [],
                    "progress": jd.get("progress"),
                    "summary": jd.get("summary"),
                    "error": jd.get("error"),
                    "created_at": jd.get("created_at") or row.get("created_at"),
                    "updated_at": jd.get("updated_at") or row.get("updated_at"),
                    "event_count": len(jd.get("events") or []),
                }
            )
        return out

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        logger.warning("list_jobs_failed", user_id=user_id, error=str(e))
        return []


async def find_active_ai_jobs() -> List[dict]:
    """Queued and processing ``ai_pipeline`` jobs → ``[{job_id, status, deployment_ids}]`` (from Supabase).

    Used to coalesce the upload flow's AI fan-out: a chunked upload (N batches) previously
    enqueued N annotate jobs for the *same* deployment, each paying the model's fixed
    per-run cost. A deployment covered by a **queued** job is skipped: that job reads its
    media only once it holds the deployment (it turns 'processing' at that moment), so it
    will include the images just registered. A deployment covered only by a
    **processing** job gets one follow-up job, which every later chunk then reuses (#284).
    """

    def _run() -> List[dict]:
        client = create_service_client()
        resp = (
            client.table("api_jobs")
            .select("id, status, job_data")
            .in_("status", [JobStatus.QUEUED.value, JobStatus.PROCESSING.value])
            .eq("job_data->>kind", "ai_pipeline")
            .execute()
        )
        out = []
        for row in rows_of(resp):
            jd = row.get("job_data") or {}
            out.append({"job_id": row["id"], "status": row.get("status") or jd.get("status"), "deployment_ids": jd.get("deployment_ids") or []})
        return out

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        logger.warning("find_active_ai_jobs_failed", error=str(e))
        return []


async def get_job(job_id: str) -> Optional[JobInfo]:
    """Read current job state (memory-first, Supabase for cross-process freshness)."""
    raw = await _get_raw(job_id)
    if not raw:
        return None

    data = json.loads(raw)
    # A non-terminal job may be executing on another process (the ARQ worker) — pick up
    # its Supabase writes when they're newer than our memory copy.
    if data.get("status") in (JobStatus.QUEUED.value, JobStatus.PROCESSING.value):
        fresh = await asyncio.to_thread(_refresh_if_newer, job_id, data.get("updated_at"))
        if fresh:
            data = json.loads(fresh)

    event_key = f"job:{job_id}:events"
    mem_events = _memory_events.get(event_key, [])
    events = [json.loads(e) for e in mem_events[-MAX_EVENTS_RETURNED:]]

    data["events"] = events
    data["event_count"] = len(mem_events)
    data.pop("_next_seq", None)

    return JobInfo(**data)


async def update_job(
    job_id: str,
    *,
    status: Optional[JobStatus] = None,
    progress: Optional[float] = None,
    result_url: Optional[str] = None,
    error: Optional[str] = None,
    message: Optional[str] = None,
    current_phase: Optional[ProgressPhase] = None,
) -> None:
    raw = await _get_raw(job_id)
    if not raw:
        logger.warning("update_job_not_found", job_id=job_id)
        return

    data = json.loads(raw)
    if status is not None:
        data["status"] = status.value
    if progress is not None:
        data["progress"] = progress
    if result_url is not None:
        data["result_url"] = result_url
    if error is not None:
        data["error"] = error
    if message is not None:
        data["message"] = message
    if current_phase is not None:
        data["current_phase"] = current_phase.value

    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    _memory_store[f"job:{job_id}"] = json.dumps(data)

    if data.get("status") in _TERMINAL_STATUSES:
        # Terminal write: drain any in-flight detached syncs FIRST (let their writes
        # land), then write the terminal state LAST and durably (awaited) so nothing
        # overwrites it back to a running state. Combined with the write-time snapshot
        # in _sync_to_supabase, this makes 'completed'/'failed' the final word. Scope
        # the drain to THIS job so another job's slow sync can't stall completion.
        await flush_pending_syncs(job_id)
        await _sync_to_supabase(job_id, flush=True)
    else:
        await _sync_to_supabase(job_id)


@contextlib.asynccontextmanager
async def job_heartbeat(job_id: str | None, interval: float = _HEARTBEAT_INTERVAL_SECONDS):
    """Keep ``api_jobs.updated_at`` fresh while a single long pipeline step runs.

    Progress is normally written only *between* steps (the ``on_step`` callback), so
    one long step — e.g. SpeciesNet over thousands of images at ~1–2 s/image — writes
    nothing for many minutes. Two consumers watch ``updated_at`` and would act on that
    silence:
      * the **KEDA** Postgres scaler holds the GPU worker on
        ``updated_at > now() - <window>`` (15 min on dev). Past the window it scales the
        worker to 0 **mid-inference, killing the job**.
      * the **stale-job reaper** fails jobs idle for 60 min.

    This runs a background task that bumps ``updated_at`` every ``interval`` seconds
    (``update_job`` with no field changes still refreshes it and syncs, and the
    ``BEFORE UPDATE`` trigger on ``api_jobs`` advances the top-level column KEDA reads).
    The task is cancelled on exit — before the caller returns and any terminal write
    lands — so it can never resurrect a finished job.

    A no-op when ``job_id`` is falsy (in-process runs with no cloud job to heartbeat).
    Relies on the heavy step running via ``asyncio.to_thread`` (SpeciesNet/BioCLIP do),
    which keeps the event loop free to fire this timer.
    """
    if not job_id:
        yield
        return

    async def _beat() -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await update_job(job_id)  # no fields → refresh updated_at + sync only
            except Exception as exc:  # noqa: BLE001 — a heartbeat must never break the pipeline
                logger.debug("job_heartbeat_failed", job_id=job_id, error=str(exc))

    task = asyncio.create_task(_beat())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def emit_event(job_id: str, event: ProgressEvent) -> None:
    event.job_id = job_id
    raw = await _get_raw(job_id)

    if raw:
        data = json.loads(raw)
        seq = data.get("_next_seq", 0)
        event.seq = seq
        data["_next_seq"] = seq + 1
        data["message"] = event.message
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        _memory_store[f"job:{job_id}"] = json.dumps(data)

    event_json = json.dumps(event.model_dump(mode="json"), default=str)
    event_key = f"job:{job_id}:events"
    _memory_events.setdefault(event_key, []).append(event_json)

    await _sync_to_supabase(job_id)


async def update_summary(
    job_id: str,
    *,
    total: Optional[int] = None,
    downloaded_inc: int = 0,
    uploaded_inc: int = 0,
    skipped_inc: int = 0,
    failed_inc: int = 0,
    started_at: Optional[datetime] = None,
    test_photos_skipped: Optional[int] = None,
) -> None:
    lock = _summary_locks.setdefault(job_id, asyncio.Lock())

    async with lock:
        raw = await _get_raw(job_id)
        if not raw:
            return

        data = json.loads(raw)
        summary = data.get("summary") or {
            "total": 0,
            "downloaded": 0,
            "uploaded": 0,
            "skipped": 0,
            "failed": 0,
            "started_at": None,
        }

        if total is not None:
            summary["total"] = total
        summary["downloaded"] += downloaded_inc
        summary["uploaded"] += uploaded_inc
        summary["skipped"] += skipped_inc
        summary["failed"] += failed_inc
        if started_at is not None:
            summary["started_at"] = started_at.isoformat()
        if test_photos_skipped is not None:
            summary["test_photos_skipped"] = test_photos_skipped

        data["summary"] = summary
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        _memory_store[f"job:{job_id}"] = json.dumps(data)

    await _sync_to_supabase(job_id)


_PHASE_START_MSG = {
    ProgressPhase.DOWNLOAD: "📥 Downloading images from Azure Storage...",
    ProgressPhase.DRIVE_UPLOAD: "☁️ Uploading images to Google Drive...",
    ProgressPhase.CLEANUP: "🧹 Cleaning up temporary files from Azure Storage...",
}

_PHASE_COMPLETE_MSG = {
    ProgressPhase.DOWNLOAD: "📥 All images downloaded from Azure Storage ✓",
    ProgressPhase.DRIVE_UPLOAD: "☁️ All images uploaded to Google Drive ✓",
    ProgressPhase.CLEANUP: "🧹 Temporary files cleaned up from Azure Storage ✓",
}


async def start_phase(job_id: str, phase: ProgressPhase) -> None:
    await update_job(job_id, current_phase=phase)
    await emit_event(
        job_id,
        ProgressEvent(
            type=EventType.PHASE_START,
            phase=phase,
            message=_PHASE_START_MSG.get(phase, f"Starting {phase.value}..."),
        ),
    )


async def complete_phase(job_id: str, phase: ProgressPhase) -> None:
    await emit_event(
        job_id,
        ProgressEvent(
            type=EventType.PHASE_COMPLETE,
            phase=phase,
            message=_PHASE_COMPLETE_MSG.get(phase, f"{phase.value} complete"),
        ),
    )
