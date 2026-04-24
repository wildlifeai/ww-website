# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared test fixtures — mock Redis, mock Supabase, test client."""

import pytest
from fastapi.testclient import TestClient

# Set env vars before importing app
import os
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co/")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")


@pytest.fixture
def client():
    """FastAPI test client."""
    from app.main import app
    return TestClient(app)
