# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for iNaturalist OAuth service and domain logic."""

import base64
import hashlib
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

# Ensure iNat config vars exist for import
os.environ.setdefault("INAT_CLIENT_ID", "test_client_id")
os.environ.setdefault("INAT_CLIENT_SECRET", "test_client_secret_for_encryption_key_derivation")
os.environ.setdefault("INAT_REDIRECT_URI", "http://localhost:8000/api/inat/callback")

from app.config import settings
from app.schemas.inaturalist import (
    INatBatchPollRequest,
    INatCallbackParams,
    INatConnectionStatus,
    INatCreateObservation,
)
from app.services import inat_oauth
from app.services.inat_oauth import (
    INAT_AUTH_URL,
    INatKeyError,
    build_authorization_url,
    decrypt_token,
    encrypt_token,
    generate_pkce_pair,
    inat_settings_problems,
    is_token_expired,
    token_key_problem,
)

# A stand-in for a registered app's secret: as long as the ones iNaturalist issues.
SOUND_SECRET = "x" * 43


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
        assert f"client_id={settings.INAT_CLIENT_ID}" in url

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
    @pytest.fixture(autouse=True)
    def _sound_secret(self, monkeypatch):
        # settings may be built before this module's setdefault runs, so set the secret here.
        monkeypatch.setattr(settings, "INAT_CLIENT_SECRET", SOUND_SECRET)

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


def _no_service_client():
    raise AssertionError("no token is read or written without a sound key")


class TestTokenKey:
    """An empty or short INAT_CLIENT_SECRET gives a key anyone can derive (#123)."""

    @pytest.mark.parametrize("secret", ["", "   ", "changeme"])
    def test_a_missing_or_short_secret_is_refused(self, monkeypatch, secret):
        monkeypatch.setattr(settings, "INAT_CLIENT_SECRET", secret)
        problem = token_key_problem()
        assert problem and "INAT_CLIENT_SECRET" in problem
        with pytest.raises(INatKeyError, match="INAT_CLIENT_SECRET"):
            encrypt_token({"access_token": "at"})

    def test_a_registered_secret_is_sound(self, monkeypatch):
        monkeypatch.setattr(settings, "INAT_CLIENT_SECRET", SOUND_SECRET)
        assert token_key_problem() is None
        assert decrypt_token(encrypt_token({"access_token": "at"})) == {"access_token": "at"}

    async def test_no_token_is_stored_or_read_without_a_sound_key(self, monkeypatch):
        monkeypatch.setattr(settings, "INAT_CLIENT_SECRET", "")
        monkeypatch.setattr(inat_oauth, "create_service_client", _no_service_client)
        with pytest.raises(INatKeyError):
            await inat_oauth.store_user_token("u1", {"api_token": "jwt"})
        assert await inat_oauth.get_user_token("u1") is None

    def test_settings_check_names_each_problem(self, monkeypatch):
        monkeypatch.setattr(settings, "INAT_CLIENT_SECRET", "")
        monkeypatch.setattr(settings, "INAT_CLIENT_ID", "")
        problems = inat_settings_problems()
        assert any("INAT_CLIENT_SECRET" in p for p in problems)
        assert any("INAT_CLIENT_ID" in p for p in problems)

        monkeypatch.setattr(settings, "INAT_CLIENT_SECRET", SOUND_SECRET)
        monkeypatch.setattr(settings, "INAT_CLIENT_ID", "app-id")
        # The site, not the API: nothing there handles the callback.
        monkeypatch.setattr(settings, "INAT_REDIRECT_URI", "https://wildlifewatcher.ai/inat/callback")
        assert [p for p in inat_settings_problems() if "INAT_REDIRECT_URI" in p]
        monkeypatch.setattr(settings, "INAT_REDIRECT_URI", "https://api.example.org/api/inat/callback")
        assert inat_settings_problems() == []


@pytest.fixture
def api(monkeypatch):
    from app.dependencies import get_current_user
    from app.main import app

    monkeypatch.setattr(settings, "FF_INAT_ENABLED", True)
    monkeypatch.setattr(settings, "INAT_CLIENT_ID", "app-id")
    monkeypatch.setattr(settings, "INAT_CLIENT_SECRET", "")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u1")
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/inat/token", {"api_token": "jwt"}),
        ("get", "/api/inat/auth", None),
        ("get", "/api/inat/status", None),
        ("post", "/api/inat/publish", {"media_ids": ["m1"]}),
    ],
)
def test_routes_refuse_to_connect_or_publish_without_a_sound_key(api, monkeypatch, method, path, body):
    async def never(*_args, **_kwargs):
        raise AssertionError("a refused call must not reach iNaturalist or the database")

    monkeypatch.setattr("app.routers.inaturalist.validate_api_token", never)
    monkeypatch.setattr("app.routers.inaturalist.writable_media_ids", never)
    resp = api.request(method, path, json=body)
    assert resp.status_code == 503
    assert "INAT_CLIENT_SECRET is not set" in resp.json()["detail"]


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
