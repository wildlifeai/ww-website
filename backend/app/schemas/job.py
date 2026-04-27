# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Job status, progress events, and result schemas for the async job system.

Provides a formal event model so the frontend can render progress
deterministically (no string parsing) with support for:

- Phase-based pipeline tracking
- Monotonic sequence numbers for safe event consumption
- Aggregate progress summaries for progress bars and ETA
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    """Lifecycle states for an async job."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"


class ProgressPhase(str, Enum):
    """Distinct pipeline phases — used for deterministic UI rendering."""

    UPLOAD = "upload"
    DOWNLOAD = "download"
    DRIVE_UPLOAD = "drive_upload"
    CLEANUP = "cleanup"


class EventType(str, Enum):
    """Structured event categories emitted by the worker."""

    JOB_STARTED = "job_started"
    PHASE_START = "phase_start"
    PHASE_COMPLETE = "phase_complete"
    PROGRESS = "progress"
    FILE_SUCCESS = "file_success"
    FILE_FAILURE = "file_failure"
    FILE_SKIP = "file_skip"
    FOLDER_CREATED = "folder_created"
    HEARTBEAT = "heartbeat"
    STALL_WARNING = "stall_warning"


class ProgressEvent(BaseModel):
    """A single structured event emitted by the worker.

    Stored in a separate Redis list (``job:{id}:events``) to avoid
    bloating the main job key.  The ``seq`` field is a monotonic counter
    per job — the frontend uses it to safely consume events even when
    the list is trimmed.
    """

    seq: int = Field(0, description="Monotonic sequence number (auto-assigned by store)")
    type: EventType
    phase: ProgressPhase
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    current: Optional[int] = None
    total: Optional[int] = None
    file_index: Optional[int] = None
    filename: Optional[str] = None
    message: str
    batch_index: Optional[int] = None
    job_id: Optional[str] = None


class ProgressSummary(BaseModel):
    """Aggregate counters — the frontend reads this for progress bars and ETA.

    ``current_phase`` lives on ``JobInfo`` only (single source of truth).
    """

    total: int = 0
    downloaded: int = 0
    uploaded: int = 0
    skipped: int = 0
    failed: int = 0
    started_at: Optional[datetime] = None


class JobInfo(BaseModel):
    """Public representation of a job's full state."""

    job_id: str
    status: JobStatus = JobStatus.QUEUED
    progress: float = Field(0.0, ge=0.0, le=1.0, description="0.0–1.0 weighted progress")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: Optional[datetime] = Field(None, description="Last time the job state was modified")
    result_url: Optional[str] = None
    error: Optional[str] = None
    message: Optional[str] = Field(None, description="Latest human-readable status message")
    current_phase: Optional[ProgressPhase] = Field(None, description="Current pipeline phase (single source of truth)")
    summary: Optional[ProgressSummary] = Field(None, description="Aggregate progress counters")
    events: list[ProgressEvent] = Field(default_factory=list, description="Recent progress events (last N)")
    event_count: int = Field(0, description="Total number of events ever emitted")


class JobCreateResponse(BaseModel):
    """Returned when a new job is enqueued."""

    job_id: str
    status: JobStatus = JobStatus.QUEUED
