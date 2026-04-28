# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""FastAPI dependency injection — auth, Supabase clients, rate limiting.

All request-scoped dependencies live here so routers stay thin.
"""

from typing import Optional

from fastapi import Depends, Header, HTTPException

from app.services import supabase_client


async def get_current_user(authorization: str = Header(...)):
    """Validate Supabase JWT from the Authorization header.

    Returns the authenticated user object, or raises 401.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid auth header")

    token = authorization.replace("Bearer ", "")
    client = supabase_client.create_anon_client()
    user_response = client.auth.get_user(token)

    if not user_response or not user_response.user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return user_response.user


async def get_optional_user(
    authorization: Optional[str] = Header(None),
):
    """Like get_current_user but returns None for unauthenticated requests."""
    if not authorization or not authorization.startswith("Bearer "):
        return None

    token = authorization.replace("Bearer ", "")
    client = supabase_client.create_anon_client()

    try:
        user_response = client.auth.get_user(token)
        if user_response and user_response.user:
            return user_response.user
    except Exception:
        pass

    return None


async def get_user_client(authorization: str = Header(...)):
    """Supabase client authenticated as the requesting user (RLS applies)."""
    token = authorization.replace("Bearer ", "")
    client = supabase_client.create_anon_client()
    client.auth.set_session(access_token=token, refresh_token="")
    return client


async def get_privileged_client():
    """Service-role Supabase client for admin operations. Use sparingly."""
    return supabase_client.create_service_client()


async def get_manager_roles(user=Depends(get_current_user)):
    """Return all roles where the user is an organisation_manager."""
    client = supabase_client.create_service_client()
    query = (
        client.table("user_roles")
        .select("scope_id, role")
        .eq("user_id", user.id)
        .eq("scope_type", "organisation")
        .eq("role", "organisation_manager")
        .eq("is_active", True)
        .is_("deleted_at", "null")
    )
    import asyncio

    roles = await asyncio.to_thread(query.execute)
    return roles.data or []
