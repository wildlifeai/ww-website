# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Integration tests for routers — health check and basic endpoint validation.

Tests that require Redis are marked with @pytest.mark.skipif to gracefully
degrade when Redis is not available (local dev without Docker).
"""

import pytest

_redis_available = False
try:
    import redis as _redis_sync
    _r = _redis_sync.Redis.from_url("redis://localhost:6379")
    _r.ping()
    _redis_available = True
    _r.close()
except Exception:
    pass

needs_redis = pytest.mark.skipif(
    not _redis_available, reason="Redis not available"
)


def test_health_check(client):
    """GET /health should return 200 with status ok."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_docs_accessible(client):
    """GET /docs should return 200 (Swagger UI)."""
    response = client.get("/docs")
    assert response.status_code == 200


def test_openapi_schema_valid(client):
    """GET /openapi.json should return valid OpenAPI schema."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "Wildlife Watcher API"
    assert schema["info"]["version"] == "2.0.0"
    assert "/health" in schema["paths"]
    assert "/api/jobs/{job_id}" in schema["paths"]


@needs_redis
def test_job_not_found(client):
    """GET /api/jobs/{unknown_id} should return 404."""
    response = client.get("/api/jobs/nonexistent-job-id")
    assert response.status_code == 404


@needs_redis
def test_sscma_catalog(client):
    """GET /api/models/sscma/catalog should return 200 with data array."""
    response = client.get("/api/models/sscma/catalog")
    assert response.status_code == 200
    body = response.json()
    assert "data" in body
