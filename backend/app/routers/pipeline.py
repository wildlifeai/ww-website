# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""AI pipeline and event clustering router — thin HTTP layer.

Provides endpoints for:
- Running AI inference pipelines on deployments
- Clustering observations into ecological events
- Computing deployment effort statistics

All business logic is delegated to domain/ modules.
"""

import asyncio

import structlog
from fastapi import APIRouter, Depends, Request

from app.authz import assert_access, require_deployment_access
from app.dependencies import get_current_user, get_privileged_client, get_verified_user, require_not_demo
from app.domain.events import cluster_deployment_events, compute_deployment_effort
from app.domain.pipeline import PipelineBusyError, run_pipeline
from app.middleware.rate_limit import limiter
from app.schemas.common import ApiError, ApiMeta, ApiResponse
from app.schemas.pipeline import ClusterEventsRequest, PipelineRunRequest

logger = structlog.get_logger()

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


# ── Pipeline Execution ───────────────────────────────────────────────


@router.post("/run")
@limiter.limit("10/minute")
async def run_inference_pipeline(
    request: Request,
    body: PipelineRunRequest,
    user=Depends(get_verified_user),
):
    """Run an AI inference pipeline on a deployment.

    Executes the specified pipeline steps (e.g. media prep, SpeciesNet)
    on all media in the target deployment. Results are written to the
    observations table with source_type='ai'.

    Requires authentication.
    """
    req_id = getattr(request.state, "request_id", None)
    # Object-level authz: the pipeline runs with the service role (RLS-bypassing),
    # so verify the caller actually has access to this deployment.
    await assert_access(user.id, deployment_id=body.deployment_id)

    try:
        result = await run_pipeline(
            deployment_id=body.deployment_id,
            steps=body.steps,
            confidence_threshold=body.confidence_threshold,
            config=body.config,
            user_id=user.id,
            only_unannotated=body.only_unannotated,
            # A request must not hang behind a run that can take an hour: say it is busy.
            wait=False,
        )
        return ApiResponse(
            data=result.model_dump(),
            meta=ApiMeta(request_id=req_id),
        )
    except PipelineBusyError as e:
        return ApiResponse(
            error=ApiError(code="PIPELINE_BUSY", message=f"{e}. Try again when it finishes.", retryable=True),
            meta=ApiMeta(request_id=req_id),
        )
    except ValueError as e:
        return ApiResponse(
            error=ApiError(code="INVALID_REQUEST", message=str(e)),
            meta=ApiMeta(request_id=req_id),
        )
    except Exception as e:
        logger.error("pipeline_run_error", error=str(e), deployment_id=body.deployment_id)
        return ApiResponse(
            error=ApiError(
                code="PIPELINE_ERROR",
                message=f"Pipeline execution failed: {e}",
                retryable=True,
            ),
            meta=ApiMeta(request_id=req_id),
        )


# ── Event Clustering ─────────────────────────────────────────────────


@router.post("/events/cluster", dependencies=[Depends(require_not_demo)])
async def cluster_events(
    request: Request,
    body: ClusterEventsRequest,
    user=Depends(get_current_user),
):
    """Cluster observations into ecological events for a deployment.

    Groups observations by species, then splits by temporal gap to form
    independent observation_events. Existing events for the deployment
    are replaced.

    Requires authentication.
    """
    req_id = getattr(request.state, "request_id", None)
    # Object-level authz: clustering replaces this deployment's events — verify access.
    await assert_access(user.id, deployment_id=body.deployment_id)

    try:
        result = await cluster_deployment_events(
            deployment_id=body.deployment_id,
            gap_minutes=body.gap_minutes,
            min_images=body.min_images,
            user_id=user.id,
        )
        return ApiResponse(
            data=result.model_dump(),
            meta=ApiMeta(request_id=req_id),
        )
    except Exception as e:
        logger.error("cluster_events_error", error=str(e), deployment_id=body.deployment_id)
        return ApiResponse(
            error=ApiError(
                code="CLUSTERING_ERROR",
                message=f"Event clustering failed: {e}",
                retryable=True,
            ),
            meta=ApiMeta(request_id=req_id),
        )


# ── Effort Computation ───────────────────────────────────────────────


@router.post("/effort/{deployment_id}", dependencies=[Depends(require_deployment_access), Depends(require_not_demo)])
async def compute_effort(
    request: Request,
    deployment_id: str,
    user=Depends(get_current_user),
):
    """Compute and store effort statistics for a deployment.

    Calculates trap nights, camera uptime, total events/media, and
    false trigger rate. Results are upserted into the deployment_effort table.

    Requires authentication.
    """
    req_id = getattr(request.state, "request_id", None)

    try:
        result = await compute_deployment_effort(deployment_id)
        return ApiResponse(
            data=result.model_dump(),
            meta=ApiMeta(request_id=req_id),
        )
    except Exception as e:
        logger.error("compute_effort_error", error=str(e), deployment_id=deployment_id)
        return ApiResponse(
            error=ApiError(
                code="EFFORT_ERROR",
                message=f"Effort computation failed: {e}",
                retryable=True,
            ),
            meta=ApiMeta(request_id=req_id),
        )


# ── Get Effort ───────────────────────────────────────────────────────


@router.get("/effort/{deployment_id}", dependencies=[Depends(require_deployment_access)])
async def get_effort(
    request: Request,
    deployment_id: str,
    user=Depends(get_current_user),
):
    """Retrieve cached effort statistics for a deployment.

    Returns the last computed values from the deployment_effort table.
    """
    req_id = getattr(request.state, "request_id", None)

    try:
        svc = await get_privileged_client()

        def _fetch():
            resp = svc.table("deployment_effort").select("*").eq("deployment_id", deployment_id).execute()
            return resp.data[0] if resp.data else None

        data = await asyncio.to_thread(_fetch)

        if not data:
            return ApiResponse(
                error=ApiError(
                    code="NOT_FOUND",
                    message="No effort data found. Run POST /api/pipeline/effort/{id} first.",
                ),
                meta=ApiMeta(request_id=req_id),
            )

        return ApiResponse(data=data, meta=ApiMeta(request_id=req_id))
    except Exception as e:
        logger.error("get_effort_error", error=str(e), deployment_id=deployment_id)
        return ApiResponse(
            error=ApiError(code="EFFORT_ERROR", message=str(e)),
            meta=ApiMeta(request_id=req_id),
        )
