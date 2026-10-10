# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""System-admin views that read across every organisation with the service role."""

import asyncio

from fastapi import APIRouter, Depends, Request

from app.authz import require_system_admin
from app.domain.admin_devices import list_devices
from app.schemas.common import ApiMeta, ApiResponse
from app.services.supabase_client import create_service_client

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/devices", response_model=ApiResponse, dependencies=[Depends(require_system_admin)])
async def admin_list_devices(request: Request) -> ApiResponse:
    """Every live device with its organisation and latest live deployment (read-only, #343).

    System admins only (``403`` otherwise): devices stay organisation-scoped under RLS
    (ww-backend#313), so this is the one place a ``ww_admin`` sees them all.
    """
    rows = await asyncio.to_thread(list_devices, create_service_client())
    return ApiResponse(data=rows, meta=ApiMeta(request_id=getattr(request.state, "request_id", None), total=len(rows)))
