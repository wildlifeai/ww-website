# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for iNaturalist OAuth service and domain logic."""

import base64
import hashlib
import os

# Ensure iNat config vars exist for import
os.environ.setdefault("INAT_CLIENT_ID", "test_client_id")
os.environ.setdefault("INAT_CLIENT_SECRET", "test_client_secret_for_encryption_key_derivation")
os.environ.setdefault("INAT_REDIRECT_URI", "http://localhost:8000/api/inat/callback")

from app.schemas.inaturalist import (
    INatBatchPollRequest,
    INatCallbackParams,
    INatConnectionStatus,
    INatCreateObservation,
)
from app.services.inat_oauth import (
    INAT_AUTH_URL,
    build_authorization_url,
    decrypt_token,
    encrypt_token,
    generate_pkce_pair,
    is_token_expired,
)


class TestPKCE:
    def test_verifier_length(self):
        verifier, challenge = generate_pkce_pair()
        assert len(verifier) > 40  # token_urlsafe(64) produces ~86 chars

    def test_challenge_is_base64(self):
        verifier, challenge = generate_pkce_pair()
        # Should be valid base64url without padding
        assert "=" not in challenge
        assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in challenge)

    def test_challenge_matches_verifier(self):
        verifier, challenge = generate_pkce_pair()
        # Independently compute expected challenge
        digest = hashlib.sha256(verifier.encode()).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        assert challenge == expected

    def test_pairs_are_unique(self):
        v1, c1 = generate_pkce_pair()
        v2, c2 = generate_pkce_pair()
        assert v1 != v2
        assert c1 != c2


class TestAuthorizationURL:
    def test_url_contains_client_id(self):
        url = build_authorization_url("test_state", "test_challenge")
        assert "client_id=test_client_id" in url

    def test_url_contains_state(self):
        url = build_authorization_url("my_state_123", "challenge")
        assert "state=my_state_123" in url

    def test_url_starts_with_inat(self):
        url = build_authorization_url("s", "c")
        assert url.startswith(INAT_AUTH_URL)

    def test_url_contains_pkce(self):
        url = build_authorization_url("s", "my_challenge")
        assert "code_challenge=my_challenge" in url
        assert "code_challenge_method=S256" in url

    def test_url_contains_response_type(self):
        url = build_authorization_url("s", "c")
        assert "response_type=code" in url


class TestTokenEncryption:
    def test_encrypt_decrypt_roundtrip(self):
        token = {
            "access_token": "at_12345",
            "refresh_token": "rt_67890",
            "expires_in": 86400,
            "obtained_at": 1700000000,
        }
        encrypted = encrypt_token(token)
        decrypted = decrypt_token(encrypted)
        assert decrypted == token

    def test_encrypted_is_not_plaintext(self):
        token = {"access_token": "secret_token_value"}
        encrypted = encrypt_token(token)
        assert "secret_token_value" not in encrypted

    def test_different_tokens_produce_different_ciphertexts(self):
        t1 = {"access_token": "token_1"}
        t2 = {"access_token": "token_2"}
        assert encrypt_token(t1) != encrypt_token(t2)


class TestTokenExpiry:
    def test_fresh_token_not_expired(self):
        import time
        token = {
            "obtained_at": int(time.time()),
            "expires_in": 86400,
        }
        assert not is_token_expired(token)

    def test_old_token_is_expired(self):
        token = {
            "obtained_at": 1000000000,  # Year 2001
            "expires_in": 3600,
        }
        assert is_token_expired(token)

    def test_buffer_window(self):
        import time
        # Token that expires in exactly 200 seconds
        token = {
            "obtained_at": int(time.time()),
            "expires_in": 200,
        }
        # With default 300s buffer, this should be "expired"
        assert is_token_expired(token, buffer_seconds=300)
        # With 100s buffer, still valid
        assert not is_token_expired(token, buffer_seconds=100)


class TestINatSchemas:
    def test_callback_params(self):
        params = INatCallbackParams(code="abc123", state="xyz789")
        assert params.code == "abc123"
        assert params.state == "xyz789"

    def test_connection_status_disconnected(self):
        status = INatConnectionStatus(connected=False)
        assert status.connected is False
        assert status.inat_username is None

    def test_connection_status_connected(self):
        status = INatConnectionStatus(
            connected=True,
            inat_username="wildlife_user",
            inat_user_id=12345,
        )
        assert status.connected is True
        assert status.inat_username == "wildlife_user"

    def test_create_observation_defaults(self):
        obs = INatCreateObservation(
            species_guess="Tui",
            latitude=-36.848,
            longitude=174.763,
            observed_on="2026-04-12",
        )
        assert obs.geoprivacy == "obscured"
        assert obs.description is None

    def test_batch_poll_request(self):
        req = INatBatchPollRequest(observation_ids=[1, 2, 3])
        assert len(req.observation_ids) == 3
