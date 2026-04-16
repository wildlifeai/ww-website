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
import json
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, List

import structlog

from app.config import settings
from app.schemas.job import (
    JobStatus,
    JobInfo,
    ProgressEvent,
    ProgressPhase,
    ProgressSummary,
    EventType,
)
from app.services.cache import get_redis

logger = structlog.get_logger()

JOB_TTL = 86400  # 24 hours
MAX_EVENTS_RETURNED = 50  # Max events returned per poll

# In-memory fallback stores
_memory_store: Dict[str, str] = {}
_memory_events: Dict[str, List[str]] = {}

# Per-job locks to serialise summary updates (avoid lost increments
# when multiple concurrent coroutines complete within the same job).
_summary_locks: Dict[str, asyncio.Lock] = {}


async def _get_redis():
    """Get the shared Redis connection. Returns None if unavailable."""
    try:
        r = await get_redis()
        await r.ping()
        return r
    except Exception:
        return None


# ── CRUD ─────────────────────────────────────────────────────────────


async def create_job() -> str:
    """Create a new job entry and return its ID."""
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
        "_next_seq": 0,  # monotonic event counter (internal)
    }

    r = await _get_redis()
    if r:
        await r.set(f"job:{job_id}", json.dumps(job_data), ex=JOB_TTL)
    else:
        logger.debug("redis_unavailable_using_memory", job_id=job_id)
        _memory_store[f"job:{job_id}"] = json.dumps(job_data)

    return job_id


async def get_job(job_id: str) -> Optional[JobInfo]:
    """Read current job state from Redis or memory fallback.

    Includes the last N events from the separate events list.
    """
    r = await _get_redis()
    if r:
        raw = await r.get(f"job:{job_id}")
    else:
        raw = _memory_store.get(f"job:{job_id}")

    if not raw:
        return None

    data = json.loads(raw)

    # Read events from separate list key
    events: list = []
    event_count = 0
    event_key = f"job:{job_id}:events"

    if r:
        event_count = await r.llen(event_key)
        if event_count > 0:
            start = max(0, event_count - MAX_EVENTS_RETURNED)
            raw_events = await r.lrange(event_key, start, -1)
            events = [json.loads(e) for e in raw_events]
    else:
        mem_events = _memory_events.get(event_key, [])
        event_count = len(mem_events)
        events = [json.loads(e) for e in mem_events[-MAX_EVENTS_RETURNED:]]

    data["events"] = events
    data["event_count"] = event_count

    # Strip internal fields before constructing the public model
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
    """Partially update a job's state in Redis or memory fallback."""
    r = await _get_redis()
    if r:
        raw = await r.get(f"job:{job_id}")
    else:
        raw = _memory_store.get(f"job:{job_id}")

    if not raw:
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

    # Always stamp updated_at so the frontend can detect stalls accurately
    data["updated_at"] = datetime.now(timezone.utc).isoformat()

    if r:
        await r.set(f"job:{job_id}", json.dumps(data), ex=JOB_TTL)
    else:
        _memory_store[f"job:{job_id}"] = json.dumps(data)


# ── Structured Event Helpers ─────────────────────────────────────────


async def emit_event(job_id: str, event: ProgressEvent) -> None:
    """Append a structured event to the job's separate event list.

    Auto-assigns a monotonic ``seq`` number from the job's internal
    counter, making it safe for the frontend to consume events even
    when the Redis list is trimmed.

    Also mirrors the ``message`` to the main job key for lightweight
    polling (clients that only read the job key still see the latest
    human-readable status).
    """
    event.job_id = job_id

    # Assign monotonic seq from the job's counter
    r = await _get_redis()
    if r:
        raw = await r.get(f"job:{job_id}")
    else:
        raw = _memory_store.get(f"job:{job_id}")

    if raw:
        data = json.loads(raw)
        seq = data.get("_next_seq", 0)
        event.seq = seq
        data["_next_seq"] = seq + 1
        data["message"] = event.message
        data["updated_at"] = datetime.now(timezone.utc).isoformat()

        if r:
            await r.set(f"job:{job_id}", json.dumps(data), ex=JOB_TTL)
        else:
            _memory_store[f"job:{job_id}"] = json.dumps(data)

    # Push to separate events list
    event_json = json.dumps(event.model_dump(mode="json"), default=str)
    event_key = f"job:{job_id}:events"

    if r:
        await r.rpush(event_key, event_json)
        await r.expire(event_key, JOB_TTL)
    else:
        _memory_events.setdefault(event_key, []).append(event_json)


async def update_summary(
    job_id: str,
    *,
    total: Optional[int] = None,
    downloaded_inc: int = 0,
    uploaded_inc: int = 0,
    skipped_inc: int = 0,
    failed_inc: int = 0,
    started_at: Optional[datetime] = None,
) -> None:
    """Atomically update the job's progress summary counters.

    Uses a per-job asyncio lock to prevent lost increments when
    multiple concurrent download/upload coroutines update simultaneously.
    """
    lock = _summary_locks.setdefault(job_id, asyncio.Lock())

    async with lock:
        r = await _get_redis()
        if r:
            raw = await r.get(f"job:{job_id}")
        else:
            raw = _memory_store.get(f"job:{job_id}")

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

        data["summary"] = summary
        data["updated_at"] = datetime.now(timezone.utc).isoformat()

        if r:
            await r.set(f"job:{job_id}", json.dumps(data), ex=JOB_TTL)
        else:
            _memory_store[f"job:{job_id}"] = json.dumps(data)


# ── Phase Lifecycle Helpers ──────────────────────────────────────────

_PHASE_START_MSG = {
    ProgressPhase.DOWNLOAD: "📥 Downloading images from Supabase...",
    ProgressPhase.DRIVE_UPLOAD: "☁️ Uploading images to Google Drive...",
    ProgressPhase.CLEANUP: "🧹 Cleaning up temporary files from Supabase...",
}

_PHASE_COMPLETE_MSG = {
    ProgressPhase.DOWNLOAD: "📥 All images downloaded from Supabase ✓",
    ProgressPhase.DRIVE_UPLOAD: "☁️ All images uploaded to Google Drive ✓",
    ProgressPhase.CLEANUP: "🧹 Temporary files cleaned up ✓",
}


async def start_phase(job_id: str, phase: ProgressPhase) -> None:
    """Mark the beginning of a pipeline phase."""
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
    """Mark the completion of a pipeline phase."""
    await emit_event(
        job_id,
        ProgressEvent(
            type=EventType.PHASE_COMPLETE,
            phase=phase,
            message=_PHASE_COMPLETE_MSG.get(phase, f"{phase.value} complete"),
        ),
    )
