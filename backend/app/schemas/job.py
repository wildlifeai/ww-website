# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Job status and result schemas for the async job system."""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


class JobStatus(str, Enum):
    """Lifecycle states for an async job."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobInfo(BaseModel):
    """Public representation of a job's current state."""

    job_id: str
    status: JobStatus = JobStatus.QUEUED
    progress: float = Field(0.0, ge=0.0, le=1.0, description="0.0–1.0 progress")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    result_url: Optional[str] = None
    error: Optional[str] = None


class JobCreateResponse(BaseModel):
    """Returned when a new job is enqueued."""

    job_id: str
    status: JobStatus = JobStatus.QUEUED
