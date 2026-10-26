# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""System-admin views that read across every organisation."""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.authz import require_system_admin
from app.dependencies import get_user_client
from app.domain.admin_devices import list_devices
from app.schemas.common import ApiMeta, ApiResponse

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/devices", response_model=ApiResponse, dependencies=[Depends(require_system_admin)])
async def admin_list_devices(request: Request, user_client: Any = Depends(get_user_client)) -> ApiResponse:
    """Every live device with its latest live deployment and that project's organisation (read-only, #343).

    System admins only (``403`` otherwise). Reads as the caller: under RLS a ``ww_admin`` sees
    every camera (ww-backend#346), deployment, project and organisation.
    """
    rows = await asyncio.to_thread(list_devices, user_client)
    return ApiResponse(data=rows, meta=ApiMeta(request_id=getattr(request.state, "request_id", None), total=len(rows)))
