# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""LoRaWAN webhook receiver endpoints.

POST /api/lorawan/webhook/ttn          — TTN v3 uplinks
POST /api/lorawan/webhook/chirpstack   — Chirpstack v4 uplinks
POST /api/lorawan/webhook/generic      — Generic LoRaWAN uplinks
GET  /api/lorawan/messages             — List messages (auth required)
GET  /api/lorawan/messages/{device_eui}/latest — Latest parsed message
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.config import settings
from app.dependencies import get_current_user
from app.domain.lorawan import LoRaWANDomain
from app.schemas.common import ApiMeta, ApiResponse
from app.schemas.lorawan import ChirpstackUplink, TTNUplink

router = APIRouter(prefix="/api/lorawan", tags=["lorawan"])
_domain = LoRaWANDomain()


def _validate_webhook_secret(provided: str, expected: str) -> None:
    """Raise 401 if the webhook secret doesn't match."""
    if not expected:
        return  # No secret configured — allow (development mode)
    if provided != expected:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")


@router.post("/webhook/ttn")
async def receive_ttn_webhook(
    payload: TTNUplink,
    request: Request,
    x_webhook_secret: str = Header("", alias="X-Webhook-Secret"),
):
    """Receive a TTN v3 uplink webhook."""
    secret = settings.LORAWAN_TTN_WEBHOOK_SECRET or settings.LORAWAN_WEBHOOK_SECRET
    _validate_webhook_secret(x_webhook_secret, secret)

    parsed = await _domain.process_ttn_uplink(payload)

    return ApiResponse(
        data=parsed.model_dump(),
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


@router.post("/webhook/chirpstack")
async def receive_chirpstack_webhook(
    payload: ChirpstackUplink,
    request: Request,
    x_webhook_secret: str = Header("", alias="X-Webhook-Secret"),
):
    """Receive a Chirpstack v4 uplink webhook."""
    _validate_webhook_secret(x_webhook_secret, settings.LORAWAN_CHIRPSTACK_WEBHOOK_SECRET or settings.LORAWAN_WEBHOOK_SECRET)

    parsed = await _domain.process_chirpstack_uplink(payload)

    return ApiResponse(
        data=parsed.model_dump(),
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


@router.get("/messages")
async def list_messages(
    request: Request,
    user=Depends(get_current_user),
):
    """List LoRaWAN messages for the authenticated user's org/project.

    TODO: Implement with Supabase RLS query scoped to user's org.
    """
    return ApiResponse(
        data=[],
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


@router.get("/messages/{device_eui}/latest")
async def latest_message(
    device_eui: str,
    request: Request,
    user=Depends(get_current_user),
):
    """Get the latest parsed LoRaWAN message for a device.

    TODO: Query lorawan_parsed_messages with RLS.
    """
    return ApiResponse(
        data=None,
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )
