# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pydantic schemas for iNaturalist integration."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class INatCallbackParams(BaseModel):
    """Query params received on the OAuth redirect callback."""

    code: str = Field(..., description="Authorization code from iNat")
    state: str = Field(..., description="CSRF state token")


class INatConnectionStatus(BaseModel):
    """Response for connection status check."""

    connected: bool
    inat_username: Optional[str] = None
    inat_user_id: Optional[int] = None
    inat_icon_url: Optional[str] = None


class INatCreateObservation(BaseModel):
    """Request to create an iNaturalist observation."""

    species_guess: str = Field(..., description="Initial species ID from AI model")
    latitude: float
    longitude: float
    observed_on: str = Field(..., description="Date observed (YYYY-MM-DD)")
    description: Optional[str] = None
    geoprivacy: str = Field("obscured", description="'open', 'obscured', or 'private'")


class INatObservationStatus(BaseModel):
    """Status/identification results for an observation."""

    id: int
    quality_grade: Optional[str] = None
    community_taxon: Optional[str] = None
    species_guess: Optional[str] = None
    identifications_count: int = 0
    identifications: Optional[List[Dict[str, Any]]] = None
    observed_on: Optional[str] = None
    uri: Optional[str] = None


class INatBatchPollRequest(BaseModel):
    """Request to poll multiple observation IDs."""

    observation_ids: List[int] = Field(
        ..., max_length=200, description="Up to 200 observation IDs"
    )
