# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the pgvector store's pure helpers + service wiring."""

import pytest

from app.services.vector_store import (
    PgVectorService,
    build_payload,
    format_vector,
    get_vector_service,
    parse_vector,
    validate_vectors,
)


def test_format_vector_is_pgvector_literal():
    assert format_vector([1.0, 2.5, -3.0]) == "[1.0,2.5,-3.0]"
    assert format_vector([]) == "[]"


def test_parse_vector_handles_string_list_and_none():
    assert parse_vector("[1.0,2.5,-3.0]") == [1.0, 2.5, -3.0]
    assert parse_vector([1, 2, 3]) == [1.0, 2.0, 3.0]
    assert parse_vector(None) is None


def test_format_parse_roundtrip():
    v = [0.123456, -0.98765, 0.0]
    assert parse_vector(format_vector(v)) == pytest.approx(v)


def test_build_payload_drops_none():
    p = build_payload(deployment_id="d1", embedding_run_id="r1", cluster_id=3, taxon_id=None)
    assert p == {"deployment_id": "d1", "embedding_run_id": "r1", "cluster_id": 3}


def test_validate_vectors_enforces_dim():
    validate_vectors([[0.0] * 384, [1.0] * 384], expected=384)  # ok
    with pytest.raises(ValueError):
        validate_vectors([[0.0] * 384, [1.0] * 383], expected=384)


def test_get_vector_service_binds_model_dim_and_caches():
    s_vits = get_vector_service("dinov3-vits")
    assert isinstance(s_vits, PgVectorService)
    assert s_vits.model_name == "dinov3-vits"
    assert s_vits.dim == 384
    # default (no model) → server ViT-H, 1280-d
    s_default = get_vector_service()
    assert s_default.model_name == "dinov3-vith"
    assert s_default.dim == 1280
    # same instance returned for the same key (cache)
    assert get_vector_service("dinov3-vits") is s_vits


# ── match_media_embeddings call (#344) ───────────────────────────────

# ww-backend's signature (supabase/schemas/public/functions/50_match_media_embeddings.sql, pinned
# by migration 20261010031215_audit_decisions_313.sql): EXECUTE for authenticated and
# service_role only, so the store must call it with the service-role client, never anon.
MATCH_FN_ARGS = {"query_embedding", "p_model", "match_count", "p_deployment_ids", "p_exclude_media_id"}
MATCH_FN_COLUMNS = ("media_id", "deployment_id", "cluster_id", "distance")


class _FakeRpcClient:
    """Records ``rpc(name, params)`` and returns canned rows, like supabase-py's builder."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def rpc(self, name, params):
        from types import SimpleNamespace

        self.calls.append((name, params))
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=self.rows))


def _store_with(monkeypatch, rows, model="dinov3-vits"):
    import app.services.vector_store as vs

    fake = _FakeRpcClient(rows)
    monkeypatch.setattr(vs, "create_service_client", lambda: fake)
    return PgVectorService(model_name=model), fake


@pytest.mark.asyncio
async def test_search_calls_match_media_embeddings_with_its_exact_arguments(monkeypatch):
    store, fake = _store_with(monkeypatch, [])
    await store.search([0.5] * 384, limit=7, conditions={"deployment_id": "d1"}, exclude_media_id="m0")

    [(name, params)] = fake.calls
    assert name == "match_media_embeddings"
    assert set(params) == MATCH_FN_ARGS
    assert params["query_embedding"] == "[" + ",".join(["0.5"] * 384) + "]"  # pgvector text literal
    assert params["p_model"] == "dinov3-vits"
    assert params["match_count"] == 7
    assert params["p_deployment_ids"] == ["d1"]  # a scalar becomes the uuid[] the function takes
    assert params["p_exclude_media_id"] == "m0"


@pytest.mark.asyncio
async def test_search_without_scope_sends_nulls(monkeypatch):
    store, fake = _store_with(monkeypatch, [])
    await store.search([0.0] * 384)
    params = fake.calls[0][1]
    assert params["p_deployment_ids"] is None and params["p_exclude_media_id"] is None
    assert params["match_count"] == 20


@pytest.mark.asyncio
async def test_search_maps_result_columns_to_similarity_points(monkeypatch):
    rows = [
        {"media_id": "m1", "deployment_id": "d1", "cluster_id": 2, "distance": 0.25},
        {"media_id": "m2", "deployment_id": "d1", "cluster_id": None, "distance": 0.5},
    ]
    assert all(set(r) == set(MATCH_FN_COLUMNS) for r in rows)
    store, _ = _store_with(monkeypatch, rows)
    hits = await store.search([1.0] * 384, conditions={"deployment_id": ["d1", "d2"]})
    assert [(h.id, h.score) for h in hits] == [("m1", 0.75), ("m2", 0.5)]  # score = 1 - cosine distance
    assert hits[0].payload == {"deployment_id": "d1", "cluster_id": 2}


@pytest.mark.asyncio
async def test_search_rejects_a_vector_of_the_wrong_dimension_before_calling(monkeypatch):
    store, fake = _store_with(monkeypatch, [])
    with pytest.raises(ValueError):
        await store.search([0.0] * 1280)  # a ViT-H vector against the ViT-S space
    assert fake.calls == []


def test_vector_store_only_uses_the_service_role_client():
    import inspect

    import app.services.vector_store as vs

    source = inspect.getsource(vs)
    assert "create_anon_client" not in source
