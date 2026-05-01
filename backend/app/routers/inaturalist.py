# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""iNaturalist integration router — OAuth flow + observation management.

Gated behind FF_INAT_ENABLED feature flag.

OAuth Flow:
  GET  /api/inat/auth        → redirect URL for iNat login
  GET  /api/inat/callback     → handle redirect from iNat
  GET  /api/inat/status       → check connection status
  POST /api/inat/disconnect   → revoke stored token

Observations:
  POST /api/inat/observations            → create observation
  GET  /api/inat/observations/{id}/status → poll identification results
  POST /api/inat/observations/poll       → batch poll multiple observations
"""

import secrets

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from app.config import settings
from app.dependencies import get_current_user
from app.domain.inaturalist import (
    INatDomainError,
    batch_poll_observations,
    create_observation,
    get_inat_user_profile,
    get_observation_status,
)
from app.schemas.common import ApiMeta, ApiResponse
from app.schemas.inaturalist import (
    INatBatchPollRequest,
    INatConnectionStatus,
    INatCreateObservation,
    INatObservationStatus,
)
from app.services.inat_oauth import (
    INatOAuthError,
    build_authorization_url,
    exchange_code_for_token,
    generate_pkce_pair,
    get_user_token,
    revoke_user_token,
    store_user_token,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/api/inat", tags=["inaturalist"])


def _check_enabled():
    """Guard: reject requests if iNat integration is disabled."""
    if not settings.FF_INAT_ENABLED:
        raise HTTPException(404, detail="iNaturalist integration is not enabled")
    if not settings.INAT_CLIENT_ID:
        raise HTTPException(500, detail="INAT_CLIENT_ID not configured")


# ── In-memory PKCE state (per-session, short-lived) ─────────────────
# In production, use Redis or a DB table for multi-instance deployments.
_pending_oauth: dict = {}  # state -> {code_verifier, user_id}


# ── OAuth Flow ───────────────────────────────────────────────────────


@router.get("/auth")
async def inat_auth(
    request: Request,
    user=Depends(get_current_user),
):
    """Start the iNat OAuth flow. Returns a URL to redirect the user to."""
    _check_enabled()

    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = generate_pkce_pair()

    # Store PKCE state for the callback
    _pending_oauth[state] = {
        "code_verifier": code_verifier,
        "user_id": user.id,
    }

    auth_url = build_authorization_url(state, code_challenge)

    return ApiResponse(
        data={"authorization_url": auth_url, "state": state},
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


@router.get("/callback")
async def inat_callback(
    code: str = Query(...),
    state: str = Query(...),
):
    """Handle the OAuth redirect from iNaturalist.

    Exchanges the authorization code for tokens, stores them encrypted,
    and redirects the user back to the web app.
    """
    _check_enabled()

    pending = _pending_oauth.pop(state, None)
    if not pending:
        raise HTTPException(400, detail="Invalid or expired OAuth state")

    code_verifier = pending["code_verifier"]
    user_id = pending["user_id"]

    try:
        token_data = await exchange_code_for_token(code, code_verifier)
    except INatOAuthError as e:
        raise HTTPException(400, detail=str(e))

    # Store encrypted token in Supabase
    await store_user_token(user_id, token_data)

    # Redirect back to frontend
    frontend_url = settings.cors_origins[0] if settings.cors_origins else "http://localhost:5173"
    return RedirectResponse(
        url=f"{frontend_url}/toolkit?inat=connected",
        status_code=302,
    )


@router.get("/status")
async def inat_status(
    request: Request,
    user=Depends(get_current_user),
):
    """Check if the current user is connected to iNaturalist."""
    _check_enabled()

    token = await get_user_token(user.id)
    if not token:
        return ApiResponse(
            data=INatConnectionStatus(connected=False).model_dump(),
            meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
        )

    # Try to get profile info
    try:
        profile = await get_inat_user_profile(user.id)
        status = INatConnectionStatus(
            connected=True,
            inat_username=profile.get("login"),
            inat_user_id=profile.get("id"),
            inat_icon_url=profile.get("icon_url"),
        )
    except INatDomainError:
        # Token exists but can't fetch profile — may be stale
        status = INatConnectionStatus(connected=True)

    return ApiResponse(
        data=status.model_dump(),
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


@router.post("/disconnect")
async def inat_disconnect(
    request: Request,
    user=Depends(get_current_user),
):
    """Disconnect from iNaturalist (revoke stored token)."""
    _check_enabled()

    await revoke_user_token(user.id)

    return ApiResponse(
        data={"disconnected": True},
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


# ── Observations ─────────────────────────────────────────────────────


@router.post("/observations")
async def create_inat_observation(
    body: INatCreateObservation,
    request: Request,
    user=Depends(get_current_user),
):
    """Create an iNaturalist observation.

    Uses the Wildlife Watcher AI model's prediction as the species_guess.
    Default geoprivacy is 'obscured' to protect sensitive locations.
    """
    _check_enabled()

    try:
        result = await create_observation(
            user_id=user.id,
            species_guess=body.species_guess,
            latitude=body.latitude,
            longitude=body.longitude,
            observed_on=body.observed_on,
            description=body.description,
            geoprivacy=body.geoprivacy,
        )
    except INatDomainError as e:
        raise HTTPException(400, detail=str(e))

    return ApiResponse(
        data=result,
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


@router.get("/observations/{observation_id}/status")
async def get_inat_observation_status(
    observation_id: int,
    request: Request,
):
    """Poll identification status for an iNat observation.

    This is a public query — no auth required for public observations.
    """
    _check_enabled()

    try:
        status = await get_observation_status(observation_id)
    except INatDomainError as e:
        raise HTTPException(404, detail=str(e))

    return ApiResponse(
        data=INatObservationStatus(**status).model_dump(),
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


@router.post("/observations/poll")
async def batch_poll_inat_observations(
    body: INatBatchPollRequest,
    request: Request,
):
    """Batch poll identification status for multiple observations."""
    _check_enabled()

    try:
        results = await batch_poll_observations(body.observation_ids)
    except INatDomainError as e:
        raise HTTPException(400, detail=str(e))

    return ApiResponse(
        data=results,
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )
