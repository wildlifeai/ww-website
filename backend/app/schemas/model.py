# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Model conversion, upload and training schemas."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ModelConvertRequest(BaseModel):
    """Request to convert a user-uploaded Edge Impulse ZIP through Vela."""

    filename: str = Field(..., description="Original filename of the uploaded ZIP")


class ModelUploadRequest(BaseModel):
    """Request to register a converted model in Supabase."""

    name: str = Field(..., description="Model display name")
    labels: List[str] = Field(default_factory=list, description="Classification labels")
    organisation_id: str = Field(..., description="Target organisation UUID")


class TrainClassSpec(BaseModel):
    """One output class of the model to train.

    ``scientific_name`` is what the selected images' observations are matched on
    (observations carry free-text ``scientific_name``; most have no taxon link).
    ``label`` is the device-facing class name written to ``labels.txt``; it is
    sanitised server-side (letters, digits, spaces, ``_`` and ``-`` only, max 32).
    """

    label: str = Field(..., min_length=1, max_length=64, description="Device class label (one line of labels.txt)")
    scientific_name: str = Field(..., min_length=1, description="Observation scientific_name that maps to this class")
    taxon_id: Optional[str] = Field(None, description="Taxon UUID when the observations carry one")
    vernacular_name: Optional[str] = Field(None)


class TrainModelRequest(BaseModel):
    """Train an on-device species classifier (a Species Brain) from selected images.

    Every selected image contributes one sample per non-edge observation: a
    target class when its ``scientific_name`` matches one of ``classes``, the
    background class when it is a blank or an unlisted species (only if
    ``include_background``), and nothing otherwise. Class order on the device is
    background first, then ``classes`` in the order given, because the firmware reports
    class index 1 as "the target" for a two-class model.
    """

    media_ids: List[str] = Field(..., min_length=2, max_length=5000, description="Selected media UUIDs from the Annotations page")
    model_name: str = Field(..., min_length=2, max_length=60)
    description: str = Field("", max_length=500)
    organisation_id: str = Field("", description="Managed organisation that owns the model (required when the user manages several)")
    classes: List[TrainClassSpec] = Field(..., min_length=1, max_length=15, description="Target classes (device MAX_CLASSES 16, background included)")
    include_background: bool = Field(True, description="Turn blanks and unlisted species in the selection into a background class")
    background_label: str = Field("", max_length=64, description="Background class label; default 'not <target>' or 'other'")
    image_size: Literal[96, 160] = Field(96, description="Model input size (96 recommended for the HX6538)")
    colour: Literal["grayscale", "rgb"] = Field("grayscale", description="Input channels; grayscale is the recipe used for the rat model")
    epochs: int = Field(30, ge=5, le=100, description="Training cycles (guide: 30 to 50)")
    learning_rate: float = Field(0.001, gt=0, le=0.1)
