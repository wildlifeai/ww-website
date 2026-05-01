# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Model conversion and upload schemas."""

from typing import List

from pydantic import BaseModel, Field


class ModelConvertRequest(BaseModel):
    """Request to convert a user-uploaded Edge Impulse ZIP through Vela."""

    filename: str = Field(..., description="Original filename of the uploaded ZIP")


class ModelUploadRequest(BaseModel):
    """Request to register a converted model in Supabase."""

    name: str = Field(..., description="Model display name")
    labels: List[str] = Field(default_factory=list, description="Classification labels")
    organisation_id: str = Field(..., description="Target organisation UUID")
