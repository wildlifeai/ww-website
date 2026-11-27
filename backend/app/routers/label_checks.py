# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Label checks (#448): a project's flags in numbers, and a recompute on request.

The flags themselves are read and marked by the website through PostgREST, under ww-backend's
``label_checks`` policies (a reader of the observation reads its flags, a project member marks
one checked). Computing them is ``domain/label_check_runs.py``, queued by ``jobs/label_checks.py``.
Gated by ``FF_LABEL_CHECKS_ENABLED``: off, both answer FEATURE_DISABLED.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request

from app.authz import require_project_access, require_project_write
from app.config import settings
from app.dependencies import get_current_user, require_not_demo
from app.domain.label_check_runs import summary
from app.jobs.label_checks import queue_project
from app.middleware.rate_limit import limiter
from app.schemas.common import ApiError, ApiMeta, ApiResponse
from app.services.supabase_client import create_service_client

router = APIRouter(prefix="/api/label-checks", tags=["label-checks"])


def _disabled(req_id):
    return ApiResponse(
        error=ApiError(code="FEATURE_DISABLED", message="Label checks are disabled (FF_LABEL_CHECKS_ENABLED)."),
        meta=ApiMeta(request_id=req_id),
    )


@router.get("/{project_id}", dependencies=[Depends(require_project_access)])
async def label_check_summary(request: Request, project_id: str, user=Depends(get_current_user)):
    """The project's open flags by kind, how many were marked checked and how many relabelled
    (design decision 15), and when they were last computed."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_LABEL_CHECKS_ENABLED:
        return _disabled(req_id)
    data = await asyncio.to_thread(summary, create_service_client(), project_id)
    return ApiResponse(data=data, meta=ApiMeta(request_id=req_id))


@router.post("/{project_id}/recompute", dependencies=[Depends(require_project_write), Depends(require_not_demo)])
@limiter.limit("6/minute")
async def recompute_label_checks(request: Request, project_id: str, user=Depends(get_current_user)):
    """Recompute the project's flags now, as a job."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_LABEL_CHECKS_ENABLED:
        return _disabled(req_id)
    job_id = await queue_project(project_id, user_id=user.id)
    return ApiResponse(data={"job_id": job_id, "status": "queued", "project_id": project_id}, meta=ApiMeta(request_id=req_id))
