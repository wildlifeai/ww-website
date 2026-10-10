# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rate limiting (slowapi).

Keyed by **authenticated user** when a Bearer JWT is present, else by client IP.
This keeps shared-NAT users from throttling each other and pins abuse to the
account, not just the IP. The JWT ``sub`` is read unverified (route handlers
still verify auth via ``get_current_user``); a forged ``sub`` only changes the
caller's own bucket, and anonymous callers have no token so fall back to IP.

Enforcement is per-route via ``@limiter.limit(...)`` on the abuse-prone /
expensive endpoints (uploads, AI pipeline, embedding). It is intentionally NOT a
global middleware, so ``/health`` probes are never throttled.
"""

import base64
import json

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from app.config import settings


def _user_or_ip(request: Request) -> str:
    """Rate-limit key: ``user:<sub>`` from the Bearer JWT, else ``ip:<addr>``."""
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        parts = auth[7:].strip().split(".")
        if len(parts) == 3:
            try:
                payload = parts[1] + "=" * (-len(parts[1]) % 4)  # pad base64url
                sub = json.loads(base64.urlsafe_b64decode(payload)).get("sub")
                if sub:
                    return f"user:{sub}"
            except Exception:
                pass  # malformed token → fall through to IP
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(
    key_func=_user_or_ip,
    default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"],
)
