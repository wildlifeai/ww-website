# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared test fixtures — mock Redis, mock Supabase, test client.

This repo includes some pure-unit tests that don't require FastAPI.
In lightweight environments (like CI or local dev without backend deps),
FastAPI may not be installed. In that case we still want unit tests to run.
"""

import pytest

try:  # pragma: no cover
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover
    TestClient = None  # type: ignore[misc,assignment]

# Set env vars before importing app
import os
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co/")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")


@pytest.fixture
def client():
    """FastAPI test client."""
    if TestClient is None:
        pytest.skip("FastAPI not installed; skipping router tests")
    from app.main import app
    return TestClient(app)
