# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""FastAPI application — entry point.

Wires together: CORS, lifespan (Redis connect/disconnect), middleware, and routers.
"""

import asyncio
import re
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.middleware.logging import LoggingMiddleware
from app.middleware.rate_limit import limiter
from app.middleware.request_id import RequestIDMiddleware
from app.routers import (
    auth,
    brain,
    camtrapdp,
    clustering,
    deployments,
    exif,
    inaturalist,
    intelligence,
    jobs,
    lorawan,
    manifest,
    media,
    models,
    pipeline,
    projects,
    public_api,
    qa,
)

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown hooks."""
    logger.info("app_startup", log_level=settings.LOG_LEVEL)

    # Optional Sentry init
    if settings.SENTRY_DSN:
        try:
            import sentry_sdk
            from sentry_sdk.integrations.fastapi import FastApiIntegration

            sentry_sdk.init(
                dsn=settings.SENTRY_DSN,
                integrations=[FastApiIntegration()],
                traces_sample_rate=0.1,
            )
            logger.info("sentry_initialized")
        except ImportError:
            logger.warning("sentry_sdk_not_installed")

    # Stale-job reaper: age-based, so it only fails jobs with no progress for an hour.
    # (The old recover_stuck_jobs failed EVERY 'processing' job at startup — wrong now
    # that AI jobs legitimately run on the separate worker process across API restarts.)
    from app.jobs.store import reap_stale_jobs

    async def _reaper_loop():
        while True:
            try:
                await reap_stale_jobs(max_age_minutes=60)
            except Exception as e:  # never let the loop die
                logger.warning("reaper_iteration_failed", error=str(e))
            await asyncio.sleep(15 * 60)

    reaper_task = asyncio.create_task(_reaper_loop())

    yield

    # Shutdown
    reaper_task.cancel()
    logger.info("app_shutdown")


app = FastAPI(
    title="Wildlife Watcher API",
    description="V2 backend — async job system, LoRaWAN ingestion, model conversion",
    version="2.0.0",
    lifespan=lifespan,
)

# ── Middleware (order matters: outermost first) ──────────────────────
app.add_middleware(RequestIDMiddleware)
app.add_middleware(LoggingMiddleware)
# Allow every Cloudflare Pages URL for this project (production alias, the `dev`
# branch alias, and per-PR previews) without listing each one:
#   ww-website.pages.dev, dev.ww-website.pages.dev, <hash>.ww-website.pages.dev
# Anchored (^…$) so the whole origin must match — without the trailing anchor an
# attacker could use https://ww-website.pages.dev.evil.com. (Starlette matches
# with re.fullmatch which already anchors, but the anchors make this safe even if
# the pattern is ever reused with re.match/re.search.)
_PAGES_DEV_RE = re.compile(r"^https://([a-z0-9-]+\.)?ww-website\.pages\.dev$")


def _origin_allowed(origin: str | None) -> bool:
    return bool(origin) and (origin in settings.cors_origins or bool(_PAGES_DEV_RE.fullmatch(origin)))


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=_PAGES_DEV_RE.pattern,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ── Unhandled-exception handler (CORS-safe) ──────────────────────────
# An uncaught exception is turned into a 500 by Starlette's ServerErrorMiddleware,
# which sits OUTSIDE the CORS middleware — so by default a 500 response carries no
# Access-Control-Allow-Origin header, and the browser reports a misleading "blocked
# by CORS policy" error that hides the real server error. Reflect the CORS headers
# here so 500s surface as actual errors to the frontend.
@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    from fastapi.responses import JSONResponse

    logger.error("unhandled_exception", path=str(request.url.path), error=str(exc))
    headers: dict[str, str] = {}
    origin = request.headers.get("origin")
    if _origin_allowed(origin):
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
        headers["Vary"] = "Origin"
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "Internal server error", "retryable": False}},
        headers=headers,
    )


# ── Routers ──────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(jobs.router)
app.include_router(deployments.router)
app.include_router(projects.router)
app.include_router(exif.router)
app.include_router(lorawan.router)
app.include_router(manifest.router)
app.include_router(models.router)
app.include_router(media.router)
app.include_router(public_api.router)
app.include_router(inaturalist.router)
app.include_router(clustering.router)
if settings.FF_CAMTRAPDP_IMPORT_ENABLED:
    app.include_router(camtrapdp.router)
if settings.FF_PIPELINE_ENABLED:
    app.include_router(pipeline.router)
if settings.FF_WILDLIFE_BRAIN_ENABLED:
    app.include_router(brain.router)
if settings.FF_ACTIVE_LEARNING_ENABLED:
    app.include_router(qa.router)
if settings.FF_INTELLIGENCE_ENABLED:
    app.include_router(intelligence.router)


# ── Health check ─────────────────────────────────────────────────────
@app.get("/health", tags=["system"])
async def health_check():
    """Simple health probe for Docker/Render health checks."""
    return {"status": "ok"}
