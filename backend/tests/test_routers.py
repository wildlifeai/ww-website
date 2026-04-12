# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Integration tests for routers — health check and basic endpoint validation."""


def test_health_check(client):
    """GET /health should return 200 with status ok."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_docs_accessible(client):
    """GET /docs should return 200 (Swagger UI)."""
    response = client.get("/docs")
    assert response.status_code == 200


def test_job_not_found(client):
    """GET /api/jobs/{unknown_id} should return 404."""
    response = client.get("/api/jobs/nonexistent-job-id")
    assert response.status_code == 404


def test_sscma_catalog(client):
    """GET /api/models/sscma/catalog should return 200 with data array."""
    response = client.get("/api/models/sscma/catalog")
    assert response.status_code == 200
    body = response.json()
    assert "data" in body
