# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rate limiting configuration using slowapi.

Default limits:
- Anonymous: 60 req/min per IP
- Authenticated: 120 req/min per user
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"],
)
