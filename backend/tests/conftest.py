# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared test fixtures — mock Redis, mock Supabase, test client."""

# Set env vars before importing app
import os
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co/")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")


@pytest.fixture
def client():
    """FastAPI test client."""
    from app.main import app

    return TestClient(app)


@pytest.fixture
def mock_supabase_client():
    """Create a mock Supabase service client."""
    mock_client = MagicMock()

    # Mock table().select().eq()...execute() chain
    def make_table_mock(return_data=None):
        table = MagicMock()
        result = MagicMock()
        result.data = return_data or []
        table.select.return_value = table
        table.insert.return_value = table
        table.update.return_value = table
        table.eq.return_value = table
        table.is_.return_value = table
        table.in_.return_value = table
        table.limit.return_value = table
        table.execute.return_value = result
        return table

    mock_client.table = MagicMock(side_effect=lambda name: make_table_mock())
    mock_client.rpc = MagicMock()
    mock_client.storage = MagicMock()

    return mock_client


@pytest.fixture
def mock_user():
    """Create a mock authenticated user."""
    user = MagicMock()
    user.id = "test-user-id-000"
    user.email = "test@wildlife.ai"
    return user
