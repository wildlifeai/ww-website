# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""API key generation, validation, and scope enforcement.

Organisation admins create keys via the web UI. Partner platforms
(Wildlife Insights, TRAPPER, etc.) use them to access the Public Data API.

Key format: ww_live_<32 hex chars>
Storage: bcrypt hash in Supabase `api_keys` table.
"""

import secrets
import hashlib
from typing import Optional, List, Dict, Any

import structlog

from app.services.supabase_client import create_service_client

logger = structlog.get_logger()

KEY_PREFIX = "ww_live_"
KEY_LENGTH = 32  # hex chars after prefix

# ── Available scopes ─────────────────────────────────────────────────

VALID_SCOPES = {
    "deployments:read",
    "devices:read",
    "telemetry:read",
    "observations:read",
    "export:camtrapdp",
    "models:read",
}


class ApiKeyError(Exception):
    """Raised on key validation failures."""

    pass


# ── Key generation ───────────────────────────────────────────────────

def generate_api_key() -> tuple[str, str]:
    """Generate a new API key and its hash.

    Returns:
        Tuple of (raw_key, sha256_hash). The raw key is shown once,
        the hash is stored in the database.
    """
    raw_secret = secrets.token_hex(KEY_LENGTH // 2)
    raw_key = f"{KEY_PREFIX}{raw_secret}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    return raw_key, key_hash


# ── Key validation ───────────────────────────────────────────────────

async def validate_api_key(
    raw_key: str,
    required_scope: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate an API key and check scope permissions.

    Args:
        raw_key: The full API key (ww_live_...).
        required_scope: If provided, the key must have this scope.

    Returns:
        The api_keys row from Supabase (id, organisation_id, scopes, etc.).

    Raises:
        ApiKeyError: If key is invalid, expired, revoked, or missing scope.
    """
    if not raw_key.startswith(KEY_PREFIX):
        raise ApiKeyError("Invalid key format")

    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:len(KEY_PREFIX) + 8]  # ww_live_ + first 8 chars

    client = create_service_client()

    try:
        response = (
            client.table("api_keys")
            .select("*")
            .eq("key_hash", key_hash)
            .is_("revoked_at", "null")
            .execute()
        )

        if not response.data:
            raise ApiKeyError("Invalid or revoked API key")

        key_record = response.data[0]

        # Check expiry
        if key_record.get("expires_at"):
            from datetime import datetime, timezone

            expires = datetime.fromisoformat(key_record["expires_at"].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > expires:
                raise ApiKeyError("API key has expired")

        # Check scope
        if required_scope and required_scope not in key_record.get("scopes", []):
            raise ApiKeyError(f"Key does not have required scope: {required_scope}")

        # Update last_used_at (fire-and-forget)
        try:
            client.table("api_keys").update(
                {"last_used_at": "now()"}
            ).eq("id", key_record["id"]).execute()
        except Exception:
            pass  # Non-critical

        logger.debug(
            "api_key_validated",
            key_prefix=key_prefix,
            org_id=key_record["organisation_id"],
            scope=required_scope,
        )

        return key_record

    except ApiKeyError:
        raise
    except Exception as e:
        raise ApiKeyError(f"Key validation failed: {e}") from e


# ── Key management ───────────────────────────────────────────────────

async def create_api_key_record(
    org_id: str,
    user_id: str,
    name: str,
    scopes: List[str],
    expires_at: Optional[str] = None,
) -> tuple[str, Dict[str, Any]]:
    """Create a new API key for an organisation.

    Args:
        org_id: Organisation UUID.
        user_id: Creating user's UUID (must be org admin).
        name: Human-readable key name (e.g. "Wildlife Insights sync").
        scopes: List of permission scopes.
        expires_at: Optional ISO timestamp for key expiry.

    Returns:
        Tuple of (raw_key, db_record). Raw key is shown once to the user.

    Raises:
        ApiKeyError: If scopes are invalid or DB insert fails.
    """
    # Validate scopes
    invalid = set(scopes) - VALID_SCOPES
    if invalid:
        raise ApiKeyError(f"Invalid scopes: {invalid}")

    raw_key, key_hash = generate_api_key()
    key_prefix = raw_key[:len(KEY_PREFIX) + 8]

    client = create_service_client()

    try:
        record = {
            "organisation_id": org_id,
            "created_by": user_id,
            "name": name,
            "key_hash": key_hash,
            "key_prefix": key_prefix,
            "scopes": scopes,
        }
        if expires_at:
            record["expires_at"] = expires_at

        response = client.table("api_keys").insert(record).execute()

        if not response.data:
            raise ApiKeyError("Failed to create API key record")

        logger.info(
            "api_key_created",
            org_id=org_id,
            name=name,
            scopes=scopes,
        )

        return raw_key, response.data[0]

    except ApiKeyError:
        raise
    except Exception as e:
        raise ApiKeyError(f"Failed to create key: {e}") from e


async def revoke_api_key(key_id: str, org_id: str) -> bool:
    """Revoke an API key by setting revoked_at.

    Args:
        key_id: The api_keys.id UUID.
        org_id: Organisation UUID (for access control).

    Returns:
        True if revoked successfully.
    """
    client = create_service_client()

    try:
        response = (
            client.table("api_keys")
            .update({"revoked_at": "now()"})
            .eq("id", key_id)
            .eq("organisation_id", org_id)
            .is_("revoked_at", "null")
            .execute()
        )

        if response.data:
            logger.info("api_key_revoked", key_id=key_id, org_id=org_id)
            return True

        return False

    except Exception as e:
        logger.error("api_key_revoke_failed", key_id=key_id, error=str(e))
        return False


async def list_api_keys(org_id: str) -> List[Dict[str, Any]]:
    """List all active (non-revoked) API keys for an organisation.

    Returns key metadata only — never the hash. The raw key is
    only shown at creation time.
    """
    client = create_service_client()

    response = (
        client.table("api_keys")
        .select("id, name, key_prefix, scopes, expires_at, last_used_at, created_at")
        .eq("organisation_id", org_id)
        .is_("revoked_at", "null")
        .order("created_at", desc=True)
        .execute()
    )

    return response.data or []
