# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Client for ww-backend's ``export-camtrap-dp`` Edge Function.

The function returns a metadata-only Camtrap DP 1.0 ZIP (ww-backend#288). Its contract,
status codes included, is in ww-backend's DATABASE_REFERENCE.md, section export-camtrap-dp.
It takes one of two callers:

- a signed-in user's JWT, and the function checks they are a project member;
- the service role key with ``organisation_id``, for an organisation API key the backend
  has already validated (ww-backend#290). The key never leaves this module.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import httpx

from app.config import settings

FUNCTION_NAME = "export-camtrap-dp"
# Reading every row of a 50,000-photo project takes the function a while.
_TIMEOUT = httpx.Timeout(300.0, connect=15.0)


class CamtrapFunctionError(Exception):
    """The function answered with an error. ``status`` is its HTTP status."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _error_message(status: int, body: bytes) -> str:
    try:
        return str(json.loads(body).get("error") or f"HTTP {status}")
    except (ValueError, AttributeError):
        return body.decode("utf-8", "replace").strip()[:300] or f"HTTP {status}"


async def download_package(body: dict, dest: Path, *, user_token: Optional[str]) -> None:
    """POST ``body`` to the function and stream the ZIP it returns into ``dest``.

    With ``user_token`` the call is made as that user. Without one it is made with the
    service role key, which the function accepts only with ``organisation_id`` in the body.
    Raises ``CamtrapFunctionError`` for any non-200 answer.
    """
    if not user_token and not body.get("organisation_id"):
        raise ValueError("Without a user token the export needs an organisation_id")
    bearer = user_token or settings.SUPABASE_SERVICE_ROLE_KEY
    url = f"{settings.SUPABASE_URL.rstrip('/')}/functions/v1/{FUNCTION_NAME}"
    headers = {"Authorization": f"Bearer {bearer}", "apikey": settings.SUPABASE_ANON_KEY}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        async with client.stream("POST", url, json=body, headers=headers) as resp:
            if resp.status_code != 200:
                raise CamtrapFunctionError(resp.status_code, _error_message(resp.status_code, await resp.aread()))
            with dest.open("wb") as out:
                async for chunk in resp.aiter_bytes():
                    out.write(chunk)
