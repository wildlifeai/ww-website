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

    observation_ids: List[int] = Field(..., max_length=200, description="Up to 200 observation IDs")


class INatAddTaxonBody(BaseModel):
    """Request body to add a taxon from iNaturalist to the local database."""

    taxon_id: int = Field(..., description="The iNaturalist taxon ID")


class INatPublishRequest(BaseModel):
    """Request to publish selected WW media to the user's iNaturalist account."""

    media_ids: List[str] = Field(..., min_length=1, max_length=200, description="WW media ids to publish")
    gap_seconds: int = Field(
        60,
        ge=1,
        le=3600,
        description="Temporal gap to consolidate images into observation bursts (one iNat observation per burst).",
    )
    geoprivacy: str = Field("obscured", description="'open', 'obscured', or 'private'")


class INatPublishResult(BaseModel):
    """Summary of an iNaturalist publish run."""

    observations_created: int = 0
    photos_uploaded: int = 0
    skipped_bycatch: int = 0
    skipped_already_published: int = 0
    errors: int = 0
    observations: List[Dict[str, Any]] = Field(default_factory=list)
    # The photos the request did not reach in its time; send them again to carry on.
    remaining_media_ids: List[str] = Field(default_factory=list)
