# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Manifest generation request/response schemas."""

from pydantic import BaseModel, Field
from typing import Optional, List


class ManifestRequest(BaseModel):
    """Request to generate a MANIFEST.zip firmware package."""

    model_source: str = Field(
        "default",
        description="'default' | 'github' | 'sscma' | 'organisation'",
    )
    # GitHub model selection
    model_type: Optional[str] = Field(None, description="Model name from registry")
    resolution: Optional[str] = Field(None, description="e.g. '192x192'")
    # SSCMA model selection
    sscma_model_id: Optional[str] = Field(None, description="SSCMA model UUID/slug")
    # Organisation model selection
    org_model_id: Optional[str] = Field(None, description="Supabase ai_models.id")
    # Camera config
    camera_type: str = Field("Raspberry Pi", description="Camera config key")
