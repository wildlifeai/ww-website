# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Manifest generation request/response schemas."""

from typing import Optional

from pydantic import BaseModel, Field


class ManifestRequest(BaseModel):
    """Request to generate a MANIFEST.zip firmware package."""

    model_source: str = Field(
        "default",
        description=(
            "'My Project' | 'Pre-trained Model' | 'SenseCap Models' "
            "| 'My Organization Models' | 'No Model'"
        ),
    )
    model_name: Optional[str] = Field(None, description="Human-readable model name")
    model_id: Optional[int] = Field(None, description="Firmware OP14 model ID")
    model_version: Optional[int] = Field(None, description="Firmware OP15 model version")
    resolution: Optional[str] = Field(None, description="e.g. '192x192'")
    # Legacy fields kept for backward compatibility
    model_type: Optional[str] = Field(None, description="Model name from registry")
    sscma_model_id: Optional[str] = Field(None, description="SSCMA model UUID/slug")
    org_model_id: Optional[str] = Field(None, description="Supabase ai_models.id")
    camera_type: str = Field("Grove Vision AI V2", description="Camera config key")
    # Project-based manifest fields
    project_id: Optional[str] = Field(
        None,
        description="Supabase projects.id — triggers project-based resolution of model + firmware IDs",
    )
    github_branch: str = Field(
        "main",
        description="Branch of wildlifeai/Seeed_Grove_Vision_AI_Module_V2 for firmware files",
    )
