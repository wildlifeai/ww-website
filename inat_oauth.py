# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later

"""iNaturalist OAuth helpers.

Stage 1 goal: authenticate a user in Streamlit and hold an access token in-session.

Notes:
- iNaturalist uses OAuth2 authorization code flow.
- Streamlit reruns the script often; keep tokens in st.session_state.
- For production, persist tokens server-side (e.g., Supabase) and consider refresh.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass
from typing import Optional, Dict
from urllib.parse import urlencode

import requests


INAT_AUTH_URL = "https://www.inaturalist.org/oauth/authorize"
INAT_TOKEN_URL = "https://www.inaturalist.org/oauth/token"


@dataclass(frozen=True)
class OAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    scope: str = "write"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def make_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("utf-8")).digest())
    return verifier, challenge


def build_authorize_url(
    cfg: OAuthConfig,
    state: str,
    code_challenge: Optional[str] = None,
) -> str:
    params = {
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "response_type": "code",
        "scope": cfg.scope,
        "state": state,
    }

    # PKCE is best practice; if the server rejects unknown params, we can disable.
    if code_challenge:
        params.update(
            {
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )

    return f"{INAT_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_token(
    cfg: OAuthConfig,
    code: str,
    code_verifier: Optional[str] = None,
) -> Dict:
    data = {
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": cfg.redirect_uri,
    }
    if code_verifier:
        data["code_verifier"] = code_verifier

    resp = requests.post(INAT_TOKEN_URL, data=data, timeout=30)
    resp.raise_for_status()
    return resp.json()


def load_oauth_config_from_env(get: callable = os.environ.get) -> Optional[OAuthConfig]:
    """Load OAuth config from env/secrets.

    Expected keys:
      - INAT_CLIENT_ID
      - INAT_CLIENT_SECRET
      - INAT_REDIRECT_URI
      - INAT_SCOPE (optional; default write)
    """

    client_id = get("INAT_CLIENT_ID")
    client_secret = get("INAT_CLIENT_SECRET")
    redirect_uri = get("INAT_REDIRECT_URI")
    scope = get("INAT_SCOPE") or "write"

    if not client_id or not client_secret or not redirect_uri:
        return None

    return OAuthConfig(
        client_id=str(client_id),
        client_secret=str(client_secret),
        redirect_uri=str(redirect_uri),
        scope=str(scope),
    )