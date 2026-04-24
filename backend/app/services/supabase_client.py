# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Supabase client factories.

Three flavours:
- anon: public key, RLS enforced (default for user-facing reads)
- user: anon client with user JWT session set (RLS scoped to user)
- service: service-role key, bypasses RLS (admin ops only)
"""

from supabase import create_client, Client
from app.config import settings


def create_anon_client() -> Client:
    """Create a Supabase client using the anonymous/public key."""
    url = settings.SUPABASE_URL
    if not url.endswith("/"):
        url += "/"
    return create_client(url, settings.SUPABASE_ANON_KEY)


def create_service_client() -> Client:
    """Create a Supabase client using the service-role key.

    ⚠️ Bypasses RLS — use only for trusted backend operations.
    """
    url = settings.SUPABASE_URL
    if not url.endswith("/"):
        url += "/"
    return create_client(url, settings.SUPABASE_SERVICE_ROLE_KEY)
