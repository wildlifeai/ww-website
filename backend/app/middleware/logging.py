# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Structured JSON logging middleware.

Logs every request with timing, status, path, method and user_id (if auth'd).
Uses structlog for machine-parseable JSON output.
"""

import time

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger()


class LoggingMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request/response as a structured JSON event."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = getattr(request.state, "request_id", "unknown")
        start = time.monotonic()

        try:
            response = await call_next(request)
        except Exception as e:
            try:
                logger.exception("unhandled_error", error=str(e))
            except Exception:
                pass  # Never let logging kill the server
            raise

        duration = time.monotonic() - start
        try:
            logger.info(
                "request_completed",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=round(duration * 1000, 2),
                user_id=getattr(request.state, "user_id", None),
            )
        except Exception:
            pass  # Never let logging kill the server
        return response
