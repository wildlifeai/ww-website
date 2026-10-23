# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""QA router — label-quality metrics, independent of active learning.

QA must be unbiased (active learning is biased by design). This slice reports
AI-vs-human agreement as a precision proxy; a fully unbiased blind random-sample
review workflow is a documented follow-up.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Request

from app.authz import require_deployment_access
from app.config import settings
from app.dependencies import get_current_user
from app.schemas.common import ApiError, ApiMeta, ApiResponse

logger = structlog.get_logger()

router = APIRouter(prefix="/api/qa", tags=["qa"])


@router.get("/report/{deployment_id}", dependencies=[Depends(require_deployment_access)])
async def qa_report_endpoint(request: Request, deployment_id: str, user=Depends(get_current_user)):
    """AI-vs-human agreement (precision proxy) for a deployment."""
    req_id = getattr(request.state, "request_id", None)
    if not settings.FF_ACTIVE_LEARNING_ENABLED:
        return ApiResponse(
            error=ApiError(code="FEATURE_DISABLED", message="QA disabled (FF_ACTIVE_LEARNING_ENABLED)."),
            meta=ApiMeta(request_id=req_id),
        )

    from app.domain.active_learning import qa_report

    return ApiResponse(data=await qa_report(deployment_id), meta=ApiMeta(request_id=req_id))
