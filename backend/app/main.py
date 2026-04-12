# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""FastAPI application — entry point.

Wires together: CORS, lifespan (Redis connect/disconnect), middleware, and routers.
"""

import structlog
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.middleware.request_id import RequestIDMiddleware
from app.middleware.logging import LoggingMiddleware
from app.middleware.rate_limit import limiter
from app.services.cache import close_redis

from app.routers import jobs, exif, lorawan, manifest, models

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

    # Create ARQ Redis pool for job enqueuing
    try:
        from arq import create_pool
        from arq.connections import RedisSettings

        app.state.arq_pool = await create_pool(
            RedisSettings.from_dsn(settings.REDIS_URL)
        )
        logger.info("arq_pool_created")
    except Exception as e:
        logger.warning("arq_pool_failed", error=str(e))
        app.state.arq_pool = None

    yield

    # Shutdown
    if hasattr(app.state, "arq_pool") and app.state.arq_pool:
        await app.state.arq_pool.close()
    await close_redis()
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── Routers ──────────────────────────────────────────────────────────
app.include_router(jobs.router)
app.include_router(exif.router)
app.include_router(lorawan.router)
app.include_router(manifest.router)
app.include_router(models.router)


# ── Health check ─────────────────────────────────────────────────────
@app.get("/health", tags=["system"])
async def health_check():
    """Simple health probe for Docker/Render health checks."""
    return {"status": "ok"}
