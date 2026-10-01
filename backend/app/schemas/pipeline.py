# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pydantic schemas for the AI pipeline and ecological event system.

Request/response models for pipeline runs, event clustering,
and deployment effort statistics.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, computed_field

# ── Pipeline Schemas ─────────────────────────────────────────────────


class PipelineStepType(str, Enum):
    """Supported pipeline step types."""

    MEDIA_PREP = "media_prep"  # thumbnails + previews → media_assets (run before SPECIESNET)
    GEMINI_PRESENCE = "gemini_presence"  # VLM animal-present verdict per frame (run before SPECIESNET, does not alter it)
    SPECIESNET = "speciesnet"  # detector + classifier ensemble (preferred)
    ANIMAL_CROP = "animal_crop"  # crop best detection → animal_crop_url (run after SPECIESNET)
    BIOCLIP = "bioclip"  # secondary zero-shot classifier on crops (run after ANIMAL_CROP)
    EVIDENCE_FUSION = "evidence_fusion"  # consensus row per frame from all of the above plus burst motion (run last)
    CUSTOM = "custom"


class PipelineRunRequest(BaseModel):
    """Request to run an AI pipeline on a deployment."""

    deployment_id: str = Field(..., description="UUID of the target deployment")
    steps: list[PipelineStepType] = Field(
        default=[PipelineStepType.SPECIESNET],
        description="Ordered list of pipeline steps to execute",
    )
    confidence_threshold: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Minimum confidence to keep a detection",
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Step-specific overrides (e.g. model path, batch_size)",
    )
    only_unannotated: bool = Field(
        default=True,
        description="Only process media without an existing AI observation (idempotent + "
        "incremental). Set false to force a full re-run over every image in the deployment.",
    )


class PipelineStepResult(BaseModel):
    """Result of a single pipeline step execution."""

    step: PipelineStepType
    observations_created: int = 0
    observations_updated: int = 0
    media_processed: int = 0
    errors: int = 0
    duration_seconds: float = 0.0
    model_version: Optional[str] = None
    # Metered steps (Gemini presence) report what the batch cost; None for local models.
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    # Step-specific tallies (evidence fusion: bursts, consensus_animal, consensus_blank,
    # evidence_rows, ...); empty for steps that have none.
    counts: dict[str, int] = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def seconds_per_frame(self) -> Optional[float]:
        """Wall-clock seconds per frame for this step, None when it processed nothing (#171).

        On the Cloud Run GPU job every second of every step is billed, so this, not only the
        GPU models' share, is what the cost per photo is built from.
        """
        return round(self.duration_seconds / self.media_processed, 3) if self.media_processed else None


class PipelineRunResult(BaseModel):
    """Aggregate result of a full pipeline run."""

    deployment_id: str
    annotation_run_id: Optional[str] = None
    steps: list[PipelineStepResult] = Field(default_factory=list)
    total_media: int = 0
    total_observations: int = 0
    duration_seconds: float = 0.0


# ── Event Clustering Schemas ─────────────────────────────────────────


class ClusterEventsRequest(BaseModel):
    """Request to cluster observations into ecological events."""

    deployment_id: str = Field(..., description="UUID of the deployment to cluster")
    gap_minutes: int = Field(
        default=30,
        ge=1,
        le=1440,
        description="Temporal gap (minutes) to split independent events",
    )
    min_images: int = Field(
        default=1,
        ge=1,
        description="Minimum media count for a valid event",
    )


class ObservationEventSummary(BaseModel):
    """Summary of a single observation event for API responses."""

    id: str
    deployment_id: str
    taxon_id: Optional[str] = None
    scientific_name: Optional[str] = None
    common_name: Optional[str] = None
    start_time: datetime
    end_time: datetime
    event_duration_seconds: int
    media_count: int
    review_status: str = "unreviewed"
    confidence: Optional[float] = None
    trigger_type: Optional[str] = None


class ClusterEventsResult(BaseModel):
    """Result of temporal event clustering."""

    deployment_id: str
    events_created: int = 0
    events_updated: int = 0
    observations_linked: int = 0
    events: list[ObservationEventSummary] = Field(default_factory=list)


# ── Effort Schemas ───────────────────────────────────────────────────


class DeploymentEffortSummary(BaseModel):
    """Computed effort statistics for a deployment."""

    deployment_id: str
    trap_nights: float = 0.0
    camera_uptime_hours: float = 0.0
    total_events: int = 0
    total_media: int = 0
    false_trigger_rate: float = 0.0
    computed_at: Optional[datetime] = None
