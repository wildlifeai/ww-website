# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""iNaturalist OAuth2 service — authorization code flow with PKCE.

Handles the server-side OAuth dance:
  1. Build authorization URL → user clicks → iNat login page
  2. iNat redirects back with ?code=...&state=...
  3. Exchange code for access_token + refresh_token
  4. Persist encrypted tokens in Supabase
  5. Refresh tokens transparently when expired

Endpoints:
  - Authorization: https://www.inaturalist.org/oauth/authorize
  - Token:         https://www.inaturalist.org/oauth/token
  - API Token:     https://www.inaturalist.org/users/api_token
  - API Base:      https://api.inaturalist.org/v1
"""

import hashlib
import base64
import secrets
import time
import json
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlencode
from cryptography.fernet import Fernet

import httpx
import structlog

from app.config import settings
from app.services.supabase_client import create_service_client

logger = structlog.get_logger()

# ── iNat OAuth constants ─────────────────────────────────────────────

INAT_AUTH_URL = "https://www.inaturalist.org/oauth/authorize"
INAT_TOKEN_URL = "https://www.inaturalist.org/oauth/token"
INAT_API_TOKEN_URL = "https://www.inaturalist.org/users/api_token"
INAT_API_BASE = "https://api.inaturalist.org/v1"


class INatOAuthError(Exception):
    """Raised on OAuth flow failures."""
    pass


# ── Encryption helpers ───────────────────────────────────────────────

def _get_fernet() -> Fernet:
    """Derive a Fernet key from INAT_CLIENT_SECRET.

    We use the client secret as a deterministic seed for the encryption key.
    This means tokens are tied to the application registration — if you
    rotate the secret, existing stored tokens become unreadable.
    """
    # Derive a 32-byte key from the client secret via SHA-256
    key_material = hashlib.sha256(
        settings.INAT_CLIENT_SECRET.encode()
    ).digest()
    fernet_key = base64.urlsafe_b64encode(key_material)
    return Fernet(fernet_key)


def encrypt_token(token_data: Dict[str, Any]) -> str:
    """Encrypt token JSON for storage in Supabase."""
    f = _get_fernet()
    plaintext = json.dumps(token_data).encode()
    return f.encrypt(plaintext).decode()


def decrypt_token(encrypted: str) -> Dict[str, Any]:
    """Decrypt token JSON from Supabase storage."""
    f = _get_fernet()
    plaintext = f.decrypt(encrypted.encode())
    return json.loads(plaintext.decode())


# ── PKCE helpers ─────────────────────────────────────────────────────

def generate_pkce_pair() -> Tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge (S256).

    Returns:
        Tuple of (code_verifier, code_challenge).
    """
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ── OAuth flow ───────────────────────────────────────────────────────

def build_authorization_url(state: str, code_challenge: str) -> str:
    """Build the iNaturalist OAuth authorization URL.

    Args:
        state: CSRF state token (stored in session or DB for validation).
        code_challenge: PKCE S256 challenge.

    Returns:
        Full URL to redirect the user to.
    """
    params = {
        "client_id": settings.INAT_CLIENT_ID,
        "redirect_uri": settings.INAT_REDIRECT_URI,
        "response_type": "code",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }

    # Use proper URL encoding so redirect_uri/state are safely encoded.
    query = urlencode(params)
    return f"{INAT_AUTH_URL}?{query}"


async def exchange_code_for_token(
    code: str,
    code_verifier: str,
) -> Dict[str, Any]:
    """Exchange an authorization code for access + refresh tokens.

    Args:
        code: The authorization code from the redirect.
        code_verifier: The PKCE verifier that matches the challenge.

    Returns:
        Token response dict with access_token, refresh_token, etc.

    Raises:
        INatOAuthError: If the exchange fails.
    """
    payload = {
        "client_id": settings.INAT_CLIENT_ID,
        "client_secret": settings.INAT_CLIENT_SECRET,
        "code": code,
        "redirect_uri": settings.INAT_REDIRECT_URI,
        "grant_type": "authorization_code",
        "code_verifier": code_verifier,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(INAT_TOKEN_URL, data=payload)

    if response.status_code != 200:
        logger.error(
            "inat_token_exchange_failed",
            status=response.status_code,
            body=response.text[:500],
        )
        raise INatOAuthError(f"Token exchange failed: {response.status_code}")

    token_data = response.json()

    # Add metadata for expiry tracking
    token_data["obtained_at"] = int(time.time())
    if "expires_in" not in token_data:
        token_data["expires_in"] = 86400  # Default 24h if not specified

    logger.info("inat_token_obtained")
    return token_data


async def refresh_access_token(refresh_token: str) -> Dict[str, Any]:
    """Refresh an expired access token.

    Args:
        refresh_token: The refresh token from the original exchange.

    Returns:
        New token response dict.

    Raises:
        INatOAuthError: If refresh fails (user must re-authorize).
    """
    payload = {
        "client_id": settings.INAT_CLIENT_ID,
        "client_secret": settings.INAT_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(INAT_TOKEN_URL, data=payload)

    if response.status_code != 200:
        raise INatOAuthError("Token refresh failed — user must re-authorize")

    token_data = response.json()
    token_data["obtained_at"] = int(time.time())
    if "expires_in" not in token_data:
        token_data["expires_in"] = 86400

    logger.info("inat_token_refreshed")
    return token_data


def is_token_expired(token_data: Dict[str, Any], buffer_seconds: int = 300) -> bool:
    """Check if a token is expired (with a safety buffer)."""
    obtained_at = token_data.get("obtained_at", 0)
    expires_in = token_data.get("expires_in", 0)
    return time.time() > (obtained_at + expires_in - buffer_seconds)


# ── Token persistence (Supabase) ─────────────────────────────────────

async def store_user_token(user_id: str, token_data: Dict[str, Any]) -> None:
    """Store encrypted iNat token in Supabase for a user.

    Uses upsert so reconnecting overwrites the old token.
    """
    encrypted = encrypt_token(token_data)
    client = create_service_client()

    client.table("inat_tokens").upsert(
        {
            "user_id": user_id,
            "encrypted_token": encrypted,
            "inat_username": token_data.get("inat_username", ""),
        },
        on_conflict="user_id",
    ).execute()

    logger.info("inat_token_stored", user_id=user_id)


async def get_user_token(user_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve and decrypt a stored iNat token.

    Automatically refreshes if expired.

    Returns:
        Token dict, or None if not connected.
    """
    client = create_service_client()

    try:
        response = (
            client.table("inat_tokens")
            .select("encrypted_token")
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as e:
        # Local/dev environments may not have the iNat tables migrated yet.
        # PostgREST raises an APIError with code PGRST205 when the table isn't
        # present in the schema cache.
        msg = str(e)
        if "PGRST205" in msg or "inat_tokens" in msg:
            logger.warning("inat_tokens_table_missing", user_id=user_id)
            return None
        raise

    if not response.data:
        return None

    try:
        token_data = decrypt_token(response.data[0]["encrypted_token"])
    except Exception as e:
        logger.error("inat_token_decrypt_failed", user_id=user_id, error=str(e))
        return None

    # Auto-refresh if expired
    if is_token_expired(token_data):
        refresh_tok = token_data.get("refresh_token")
        if not refresh_tok:
            return None

        try:
            token_data = await refresh_access_token(refresh_tok)
            await store_user_token(user_id, token_data)
        except INatOAuthError:
            # Refresh failed — user needs to re-authorize
            return None

    return token_data


async def revoke_user_token(user_id: str) -> bool:
    """Delete stored iNat token (disconnect)."""
    client = create_service_client()

    response = (
        client.table("inat_tokens")
        .delete()
        .eq("user_id", user_id)
        .execute()
    )

    logger.info("inat_token_revoked", user_id=user_id)
    return True


# ── API JWT helper ────────────────────────────────────────────────────

async def get_api_jwt(access_token: str) -> str:
    """Exchange an iNat OAuth access token for a short-lived API JWT.

    The JWT is required for write operations on api.inaturalist.org.
    """
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            INAT_API_TOKEN_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if response.status_code != 200:
        raise INatOAuthError("Failed to obtain iNat API JWT")

    return response.json().get("api_token", "")
