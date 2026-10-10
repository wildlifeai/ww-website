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

The public API (/api/v1) counts per API key instead, with :func:`hit_api_key_limit`
in its auth dependency: the key is only known once it has been validated.
"""

import base64
import json
import math
import time
from typing import Optional

from limits import RateLimitItemPerMinute
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


def hit_api_key_limit(key_id: str, per_minute: int, bucket: str = "all") -> Optional[int]:
    """Count one call by an API key against ``per_minute`` in ``bucket``, in the limiter's storage.

    Returns None when the call is allowed, else the seconds until the key's window resets,
    for ``Retry-After``.
    """
    if not limiter.enabled:
        return None
    item = RateLimitItemPerMinute(per_minute)
    if limiter.limiter.hit(item, "apikey", bucket, key_id):
        return None
    reset_at, _ = limiter.limiter.get_window_stats(item, "apikey", bucket, key_id)
    return max(1, math.ceil(reset_at - time.time()))
