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

import asyncio
import secrets
import time

import httpx
import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from app.authz import WRITE_REFUSED, writable_media_ids
from app.config import settings
from app.dependencies import get_current_user, require_not_demo
from app.domain.inaturalist import (
    INatDomainError,
    batch_poll_observations,
    create_observation,
    get_inat_user_profile,
    get_observation_status,
)
from app.domain.inaturalist_publish import publish_media_to_inat
from app.domain.inaturalist_sync import sync_inat_identifications
from app.schemas.common import ApiMeta, ApiResponse
from app.schemas.inaturalist import (
    INatAddTaxonBody,
    INatBatchPollRequest,
    INatConnectionStatus,
    INatCreateObservation,
    INatObservationStatus,
    INatPublishRequest,
)
from app.services.inat_oauth import (
    INAT_API_BASE,
    INatOAuthError,
    build_authorization_url,
    exchange_code_for_token,
    generate_pkce_pair,
    get_user_token,
    revoke_user_token,
    store_user_token,
    token_key_problem,
    validate_api_token,
)
from app.services.supabase_client import create_service_client

logger = structlog.get_logger()

router = APIRouter(prefix="/api/inat", tags=["inaturalist"])


def _check_feature():
    """Guard: reject requests if the iNat feature flag is off.

    Used by the Pathway-2 (pasted personal token) endpoints, which need no OAuth
    app — only the feature flag.
    """
    if not settings.FF_INAT_ENABLED:
        raise HTTPException(404, detail="iNaturalist integration is not enabled")


def _check_tokens():
    """Guard for the routes that store or read a user's token: the flag and a sound key."""
    _check_feature()
    problem = token_key_problem()
    if problem:
        raise HTTPException(503, detail=problem)


def _check_enabled():
    """Guard for the OAuth (Pathway-1) endpoints: feature flag, a sound key, a registered app."""
    _check_tokens()
    if not settings.INAT_CLIENT_ID:
        raise HTTPException(500, detail="INAT_CLIENT_ID not configured")


# ── In-memory PKCE state (per-session, short-lived) ─────────────────
# In production, use Redis or a DB table for multi-instance deployments.
_pending_oauth: dict = {}  # state -> {code_verifier, user_id}


# ── OAuth Flow ───────────────────────────────────────────────────────


@router.get("/auth", dependencies=[Depends(require_not_demo)])
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


@router.post("/token", dependencies=[Depends(require_not_demo)])
async def inat_set_token(
    request: Request,
    api_token: str = Body(..., embed=True, description="Personal iNaturalist API JWT from /users/api_token"),
    user=Depends(get_current_user),
):
    """Connect via a manually-pasted personal API token (Pathway 2 — no OAuth app).

    The user copies their JWT from https://www.inaturalist.org/users/api_token and
    pastes it here. We validate it against iNat, then store it like any other token
    bundle so publish/sync work unchanged. The JWT is ~24h-lived, so reconnecting is
    just re-pasting a fresh one.
    """
    _check_tokens()

    jwt = (api_token or "").strip()
    try:
        username = await validate_api_token(jwt)
    except INatOAuthError as e:
        raise HTTPException(400, detail=str(e))

    await store_user_token(
        user.id,
        {
            "api_token": jwt,
            "manual": True,
            "inat_username": username,
            "obtained_at": int(time.time()),
            "expires_in": 86400,  # iNat personal JWTs last ~24h
        },
    )

    return ApiResponse(
        data=INatConnectionStatus(connected=True, inat_username=username).model_dump(),
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


@router.get("/status")
async def inat_status(
    request: Request,
    user=Depends(get_current_user),
):
    """Check if the current user is connected to iNaturalist."""
    _check_tokens()

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


@router.post("/disconnect", dependencies=[Depends(require_not_demo)])
async def inat_disconnect(
    request: Request,
    user=Depends(get_current_user),
):
    """Disconnect from iNaturalist (revoke stored token)."""
    _check_feature()

    await revoke_user_token(user.id)

    return ApiResponse(
        data={"disconnected": True},
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


# ── Observations ─────────────────────────────────────────────────────


@router.post("/observations", dependencies=[Depends(require_not_demo)])
async def create_inat_observation(
    body: INatCreateObservation,
    request: Request,
    user=Depends(get_current_user),
):
    """Create an iNaturalist observation.

    Uses the Wildlife Watcher AI model's prediction as the species_guess.
    Default geoprivacy is 'obscured' to protect sensitive locations.
    """
    _check_tokens()

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


@router.post("/publish", dependencies=[Depends(require_not_demo)])
async def publish_to_inat(
    body: INatPublishRequest,
    request: Request,
    user=Depends(get_current_user),
):
    """Publish selected Wildlife Watcher media to the user's iNaturalist account.

    Consolidates the selection into temporal-burst observations (one iNat
    observation per encounter), drops by-catch (a photo whose label is not an
    animal, or that shows a person), uploads each photo through the backend proxy
    (bypassing browser CORS), and records the mapping in inat_observations /
    inat_observation_media for status tracking.

    Starts no photo after about two minutes, inside the ingress timeout, and returns the photos
    it did not reach as ``remaining_media_ids``: send those again to carry on, the rest of a
    burst joining the observation it started (``useINat().publish`` does this).

    Only photos in projects the caller can change (project_member or above) are published: the
    others are left out, and ``403`` when that is all of them.
    """
    _check_tokens()

    media_ids = await writable_media_ids(user.id, body.media_ids)
    if not media_ids:
        raise HTTPException(403, detail=WRITE_REFUSED)

    try:
        result = await publish_media_to_inat(
            user_id=user.id,
            media_ids=media_ids,
            gap_seconds=body.gap_seconds,
            geoprivacy=body.geoprivacy,
        )
    except INatDomainError as e:
        raise HTTPException(400, detail=str(e))

    return ApiResponse(
        data=result,
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


@router.post("/sync", dependencies=[Depends(require_not_demo)])
async def sync_inat(
    request: Request,
    user=Depends(get_current_user),
):
    """Pull community identifications from iNaturalist back into Wildlife Watcher.

    Polls the public iNat API for the taxon + quality_grade of this user's
    published observations, updates the inat_observations sync state (drives the
    thumbnail badge), and writes the community taxon back into WW observations as
    source_type='consensus'. A scheduled job can call the same domain function
    with no user filter to sync everyone.
    """
    _check_feature()

    result = await sync_inat_identifications(user_id=user.id)
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
    _check_feature()

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
    _check_feature()

    try:
        results = await batch_poll_observations(body.observation_ids)
    except INatDomainError as e:
        raise HTTPException(400, detail=str(e))

    return ApiResponse(
        data=results,
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


@router.get("/taxa/search")
async def search_inat_taxa(
    request: Request,
    q: str = Query(..., description="Taxon search query"),
    user=Depends(get_current_user),
):
    """Search for taxa on the public iNaturalist API."""
    if not settings.FF_INAT_ENABLED:
        raise HTTPException(404, detail="iNaturalist integration is not enabled")

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            f"{INAT_API_BASE}/taxa/autocomplete",
            params={"q": q, "per_page": 10},
        )

    if response.status_code != 200:
        raise HTTPException(400, detail="Failed to search iNaturalist taxa")

    return ApiResponse(
        data=response.json().get("results", []),
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


@router.post("/taxa", dependencies=[Depends(require_not_demo)])
async def add_inat_taxon(
    body: INatAddTaxonBody,
    request: Request,
    user=Depends(get_current_user),
):
    """Fetch complete taxonomic lineage from iNat and register in local taxa table."""
    if not settings.FF_INAT_ENABLED:
        raise HTTPException(404, detail="iNaturalist integration is not enabled")

    taxon_id = body.taxon_id

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{INAT_API_BASE}/taxa/{taxon_id}")

    if response.status_code != 200:
        raise HTTPException(400, detail=f"Failed to fetch taxon {taxon_id} from iNaturalist")

    results = response.json().get("results", [])
    if not results:
        raise HTTPException(404, detail="Taxon not found on iNaturalist")

    taxon = results[0]
    rank = taxon.get("rank")

    allowed_ranks = {"kingdom", "phylum", "class", "order", "family", "genus", "species", "subspecies"}
    if rank not in allowed_ranks:
        raise HTTPException(400, detail=f"Taxon rank '{rank}' is not supported. Supported ranks are {allowed_ranks}")

    # Extract lineage
    lineage = {"kingdom": None, "phylum": None, "class": None, "order_name": None, "family": None, "genus": None, "species": None}

    for ancestor in taxon.get("ancestors", []):
        a_rank = ancestor.get("rank")
        a_name = ancestor.get("name")
        if a_rank == "kingdom":
            lineage["kingdom"] = a_name
        elif a_rank == "phylum":
            lineage["phylum"] = a_name
        elif a_rank == "class":
            lineage["class"] = a_name
        elif a_rank == "order":
            lineage["order_name"] = a_name
        elif a_rank == "family":
            lineage["family"] = a_name
        elif a_rank == "genus":
            lineage["genus"] = a_name

    # Include current taxon rank info
    name = taxon.get("name")
    if rank == "kingdom":
        lineage["kingdom"] = name
    elif rank == "phylum":
        lineage["phylum"] = name
    elif rank == "class":
        lineage["class"] = name
    elif rank == "order":
        lineage["order_name"] = name
    elif rank == "family":
        lineage["family"] = name
    elif rank == "genus":
        lineage["genus"] = name
    elif rank in ("species", "subspecies"):
        lineage["species"] = name

    # Handle conservation status
    conservation_status = None
    cs_obj = taxon.get("conservation_status")
    if cs_obj and isinstance(cs_obj, dict):
        conservation_status = cs_obj.get("status")
    else:
        cs_list = taxon.get("conservation_statuses", [])
        if cs_list and isinstance(cs_list, list):
            conservation_status = cs_list[0].get("status")

    # Check if already exists in DB to prevent conflicts
    db_client = create_service_client()

    def check_existing():
        return db_client.table("taxa").select("*").eq("scientific_name", name).execute()

    existing = await asyncio.to_thread(check_existing)
    if existing.data:
        return ApiResponse(
            data=existing.data[0],
            meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
        )

    new_taxon = {
        "scientific_name": name,
        "common_name": taxon.get("preferred_common_name") or taxon.get("name"),
        "rank": rank,
        "kingdom": lineage["kingdom"],
        "phylum": lineage["phylum"],
        "class": lineage["class"],
        "order_name": lineage["order_name"],
        "family": lineage["family"],
        "genus": lineage["genus"],
        "species": lineage["species"],
        "gbif_taxon_id": str(taxon.get("gbif_id")) if taxon.get("gbif_id") else None,
        "inat_taxon_id": str(taxon_id),
        "nzor_id": None,
        "conservation_status": conservation_status,
        "invasive_status": False,
    }

    def insert_taxon():
        return db_client.table("taxa").insert(new_taxon).execute()

    insert_res = await asyncio.to_thread(insert_taxon)
    if not insert_res.data:
        raise HTTPException(500, detail="Failed to insert taxon into database")

    return ApiResponse(
        data=insert_res.data[0],
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )
