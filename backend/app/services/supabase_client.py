# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Supabase client factories.

Three flavours:
- anon: public key, RLS enforced (default for user-facing reads)
- user: anon client with user JWT session set (RLS scoped to user)
- service: service-role key, bypasses RLS (admin ops only)
"""

from typing import Optional

import httpx
from supabase import Client, create_client

from app.config import settings

# Process-wide cached service client. The service-role client is stateless across
# callers (no per-user session is ever set on it — unlike the anon/user client in
# get_user_client), so one shared instance is reused instead of rebuilding an httpx
# session at every one of its ~80 call sites.
_service_client: Optional[Client] = None


def create_anon_client() -> Client:
    """Create a Supabase client using the anonymous/public key.

    Not cached: the anon client is mutated per-request with the caller's JWT
    (see ``get_user_client``), so each caller needs its own instance.
    """
    url = settings.SUPABASE_URL
    if not url.endswith("/"):
        url += "/"
    return create_client(url, settings.SUPABASE_ANON_KEY)


def create_service_client() -> Client:
    """Return the process-wide Supabase service-role client (lazily built, cached).

    ⚠️ Bypasses RLS — use only for trusted backend operations. Safe to share because
    nothing sets a per-user session on it; for a deliberately fresh instance, reset
    the module-level ``_service_client`` to ``None`` first.
    """
    global _service_client
    if _service_client is None:
        url = settings.SUPABASE_URL
        if not url.endswith("/"):
            url += "/"
        _service_client = create_client(url, settings.SUPABASE_SERVICE_ROLE_KEY)
    return _service_client


def reset_service_client() -> None:
    """Drop the cached service client so the next call builds a fresh one.

    The shared client keeps one HTTP/2 connection. When Supabase (or a proxy in
    between) closes it, every caller sees ``ConnectionTerminated`` until a new
    connection is made; callers that can safely retry reset the client first.
    """
    global _service_client
    _service_client = None


def is_dropped_connection(exc: BaseException) -> bool:
    """A dropped or protocol-broken connection to Supabase, worth one retry on a fresh client.

    The server closes the shared client's connection when it sits idle, as it does while a Lambda
    environment is frozen between invocations, and the next request on it fails with
    ``Server disconnected`` (AWS dev, 2026-11-20). A request handler and a background job sharing
    it both see ``ConnectionTerminated`` at once; on the 2026-09-05 bench run that cost a whole
    upload batch its Drive job while the response still read 200.
    """
    if isinstance(exc, (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError, httpx.ConnectError)):
        return True
    return "ConnectionTerminated" in repr(exc)
