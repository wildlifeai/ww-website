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
from typing import Dict, List, Optional

import structlog

from app.schemas.job import (
    EventType,
    JobInfo,
    JobStatus,
    ProgressEvent,
    ProgressPhase,
)

logger = structlog.get_logger()

# In-memory stores (Primary fast path)
_memory_store: Dict[str, str] = {}
_memory_events: Dict[str, List[str]] = {}

# Per-job locks to serialise summary updates
_summary_locks: Dict[str, asyncio.Lock] = {}

MAX_EVENTS_RETURNED = 50


async def _sync_to_supabase(job_id: str) -> None:
    """Synchronize local memory state to Supabase in the background."""
    raw_data = _memory_store.get(f"job:{job_id}")
    if not raw_data:
        return

    mem_events = _memory_events.get(f"job:{job_id}:events", [])
    events_json = [json.loads(e) for e in mem_events]
    data_json = json.loads(raw_data)

    # Bundle events inside the data for storage
    data_json["events"] = events_json

    def _run_sync():
        try:
            from app.services.supabase_client import create_service_client

            client = create_service_client()
            status_val = data_json.get("status", "queued")
            client.table("api_jobs").upsert({"id": job_id, "status": status_val, "job_data": data_json}).execute()
        except Exception as e:
            logger.debug("supabase_sync_skipped", error=str(e))

    asyncio.create_task(asyncio.to_thread(_run_sync))


async def recover_stuck_jobs() -> None:
    """Load jobs from Supabase on startup and mark interrupted ones as failed."""
    try:
        from app.services.supabase_client import create_service_client

        client = create_service_client()
        resp = client.table("api_jobs").select("id, job_data, status").eq("status", "processing").execute()

        for row in resp.data:
            job_id = row["id"]
            data = row.get("job_data", {})
            data["status"] = JobStatus.FAILED.value
            data["error"] = "Job interrupted by server restart."
            data["message"] = "❌ Failed: Server crashed or restarted mid-job."

            # Sync back failure to DB and load to memory
            client.table("api_jobs").update({"status": data["status"], "job_data": data}).eq("id", job_id).execute()
            _memory_store[f"job:{job_id}"] = json.dumps(data)
            logger.warning("stuck_job_recovered_and_failed", job_id=job_id)

    except Exception as e:
        logger.debug("job_recovery_skipped", error=str(e))


async def create_job() -> str:
    """Create a new job entry locally and sync to Supabase."""
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
        "_next_seq": 0,
    }

    _memory_store[f"job:{job_id}"] = json.dumps(job_data)
    await _sync_to_supabase(job_id)
    return job_id


async def get_job(job_id: str) -> Optional[JobInfo]:
    """Read current job state from memory."""
    raw = _memory_store.get(f"job:{job_id}")
    if not raw:
        # Try loading from Supabase if not in memory
        try:
            from app.services.supabase_client import create_service_client

            client = create_service_client()
            resp = client.table("api_jobs").select("job_data").eq("id", job_id).execute()
            if resp.data:
                db_data = resp.data[0]["job_data"]
                events = db_data.pop("events", [])

                # Restore to memory
                _memory_events[f"job:{job_id}:events"] = [json.dumps(e) for e in events]
                _memory_store[f"job:{job_id}"] = json.dumps(db_data)
                raw = _memory_store[f"job:{job_id}"]
        except Exception:
            pass

    if not raw:
        return None

    data = json.loads(raw)

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

    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    _memory_store[f"job:{job_id}"] = json.dumps(data)

    # Background sync
    await _sync_to_supabase(job_id)


async def emit_event(job_id: str, event: ProgressEvent) -> None:
    event.job_id = job_id
    raw = _memory_store.get(f"job:{job_id}")

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
) -> None:
    lock = _summary_locks.setdefault(job_id, asyncio.Lock())

    async with lock:
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
