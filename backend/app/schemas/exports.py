# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Request schemas for data exports."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class CamtrapExportRequest(BaseModel):
    """One project, narrowed as ww-backend's export-camtrap-dp narrows it."""

    project_id: UUID
    deployment_ids: list[UUID] = Field(default_factory=list, max_length=500, description="Only these deployments of the project")
    date_from: Optional[datetime] = Field(None, description="Deployments that started at or after this time")
    date_to: Optional[datetime] = Field(None, description="Deployments that started at or before this time")
