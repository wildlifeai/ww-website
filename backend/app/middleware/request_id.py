# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Inject a unique X-Request-ID into every request/response for traceability."""

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a UUID4 request ID to every request.

    The ID is:
    - Set on ``request.state.request_id``
    - Returned as the ``X-Request-ID`` response header
    - Available for downstream logging / error tracking
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Accept client-supplied ID or generate a fresh one
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
