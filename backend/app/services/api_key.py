# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""API key generation, validation, and scope enforcement.

Organisation managers create keys in Settings. Partner platforms
(Wildlife Insights, TRAPPER, etc.) use them to access the Public Data API.

Key format: ww_live_<32 hex chars>
Storage: SHA-256 hex of the raw key in the ww-backend `api_keys` table, read and
written only with the service role. A fast lookup hash is right for a 128-bit
random key; a slow password hash such as bcrypt buys nothing here.
"""

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog

from app.services.db_utils import rows_of
from app.services.supabase_client import create_service_client

logger = structlog.get_logger()

KEY_PREFIX = "ww_live_"
KEY_LENGTH = 32  # hex chars after prefix
_LIST_PAGE = 1000  # PostgREST's row cap

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


class ApiKeyScopeError(ApiKeyError):
    """The key is valid but lacks the scope the call needs."""


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
    key_prefix = raw_key[: len(KEY_PREFIX) + 8]  # ww_live_ + first 8 chars

    client = create_service_client()

    try:
        response = (
            client.table("api_keys").select("id, organisation_id, scopes, expires_at").eq("key_hash", key_hash).is_("revoked_at", "null").execute()
        )

        if not response.data:
            raise ApiKeyError("Invalid or revoked API key")

        key_record = rows_of(response)[0]

        # Check expiry
        if key_record.get("expires_at"):
            expires = datetime.fromisoformat(key_record["expires_at"].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > expires:
                raise ApiKeyError("API key has expired")

        # Check scope
        if required_scope and required_scope not in key_record.get("scopes", []):
            raise ApiKeyScopeError(f"Key does not have required scope: {required_scope}")

        # Update last_used_at (fire-and-forget). Postgres reads the string "now()" as
        # the transaction time, like "now", so PostgREST stores a real timestamp.
        try:
            client.table("api_keys").update({"last_used_at": "now()"}).eq("id", key_record["id"]).execute()
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
    expires_at: Optional[datetime] = None,
) -> tuple[str, Dict[str, Any]]:
    """Create a new API key for an organisation.

    Args:
        org_id: Organisation UUID.
        user_id: Creating user's UUID. The caller checks they manage the organisation.
        name: Human-readable key name (e.g. "Wildlife Insights sync").
        scopes: List of permission scopes, at least one.
        expires_at: Optional expiry, in the future. A naive time is read as UTC.

    Returns:
        Tuple of (raw_key, db_record). Raw key is shown once to the user.

    Raises:
        ApiKeyError: If scopes are invalid or DB insert fails.
    """
    if not scopes:
        raise ApiKeyError("A key needs at least one scope")
    invalid = set(scopes) - VALID_SCOPES
    if invalid:
        raise ApiKeyError(f"Invalid scopes: {sorted(invalid)}")
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise ApiKeyError("expires_at must be in the future")

    raw_key, key_hash = generate_api_key()
    key_prefix = raw_key[: len(KEY_PREFIX) + 8]

    client = create_service_client()

    try:
        record = {
            "organisation_id": org_id,
            "created_by": user_id,
            "name": name,
            "key_hash": key_hash,
            "key_prefix": key_prefix,
            "scopes": sorted(set(scopes)),
        }
        if expires_at is not None:
            record["expires_at"] = expires_at.isoformat()

        response = client.table("api_keys").insert(record).execute()

        if not response.data:
            raise ApiKeyError("Failed to create API key record")

        logger.info(
            "api_key_created",
            org_id=org_id,
            name=name,
            scopes=scopes,
        )

        return raw_key, rows_of(response)[0]

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
    """List all active (non-revoked) API keys for an organisation, newest first.

    Returns key metadata only, never the hash. The raw key is
    only shown at creation time. Paged, since PostgREST returns at most
    1,000 rows a request.
    """
    client = create_service_client()

    rows: List[Dict[str, Any]] = []
    while True:
        page = rows_of(
            client.table("api_keys")
            .select("id, name, key_prefix, scopes, expires_at, last_used_at, created_at")
            .eq("organisation_id", org_id)
            .is_("revoked_at", "null")
            .order("created_at", desc=True)
            .order("id")
            .range(len(rows), len(rows) + _LIST_PAGE - 1)
            .execute()
        )
        rows.extend(page)
        if len(page) < _LIST_PAGE:
            return rows
