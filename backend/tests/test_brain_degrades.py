# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Wildlife Brain is on by default (#344): a process that can't embed degrades cleanly.

No ML stack, no HF_TOKEN for the gated DINOv3 weights, or a CUDA device that isn't there
must skip or fail the embed with a clear reason, never write a failed run per upload, never
supersede the clusters a deployment already has, and never turn a read route into a 500.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.config import settings
from app.domain import embedding_lifecycle, wildlife_brain
from app.services import dinov3


def test_brain_flag_defaults_on_and_its_neighbours_stay_off():
    fields = type(settings).model_fields
    assert fields["FF_WILDLIFE_BRAIN_ENABLED"].default is True
    assert fields["FF_ACTIVE_LEARNING_ENABLED"].default is False
    assert fields["FF_LOCAL_EMBEDDING_ENABLED"].default is False


# ── unavailable_reason ───────────────────────────────────────────────


@pytest.fixture
def ml_ready(monkeypatch):
    """Pretend the ML stack, a token and a GPU are all present; each test removes one."""
    monkeypatch.setattr(dinov3, "_missing_ml_modules", lambda: [])
    monkeypatch.setattr(dinov3, "_hf_token", lambda: "hf_test")
    monkeypatch.setattr(dinov3, "_cuda_available", lambda: True)
    monkeypatch.setattr(settings, "EMBEDDING_DEVICE", "cuda")


def test_ready_process_has_no_reason(ml_ready):
    assert dinov3.unavailable_reason() is None


def test_missing_ml_stack_is_named(ml_ready, monkeypatch):
    monkeypatch.setattr(dinov3, "_missing_ml_modules", lambda: ["torch", "umap"])
    assert dinov3.unavailable_reason() == "the ML stack is not installed in this image (torch, umap)"


def test_gated_weights_without_a_token(ml_ready, monkeypatch):
    monkeypatch.setattr(dinov3, "_hf_token", lambda: None)
    reason = dinov3.unavailable_reason("dinov3-vith")
    assert reason is not None and "HF_TOKEN" in reason and "dinov3-vith16plus" in reason


def test_cuda_device_without_a_gpu(ml_ready, monkeypatch):
    monkeypatch.setattr(dinov3, "_cuda_available", lambda: False)
    assert "no CUDA device" in dinov3.unavailable_reason()


def test_cpu_device_needs_no_gpu(ml_ready, monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_DEVICE", "cpu")
    monkeypatch.setattr(dinov3, "_cuda_available", lambda: pytest.fail("cuda probed for a cpu device"))
    assert dinov3.unavailable_reason() is None


def test_hf_token_prefers_the_setting(monkeypatch):
    monkeypatch.setattr(settings, "HF_TOKEN", "hf_from_env")
    assert dinov3._hf_token() == "hf_from_env"


# ── The embed paths when the process can't embed ─────────────────────


@pytest.fixture
def cannot_embed(monkeypatch):
    monkeypatch.setattr(dinov3, "_missing_ml_modules", lambda: ["torch"])


async def test_embed_writes_no_run_when_it_cannot_run(cannot_embed, monkeypatch):
    created = []
    monkeypatch.setattr(wildlife_brain, "_create_embedding_run", lambda *a, **k: created.append(a))
    with pytest.raises(wildlife_brain.EmbeddingUnavailableError, match="can't embed"):
        await wildlife_brain.embed_and_cluster_deployment("d1")
    assert created == []


async def test_reprocess_keeps_current_clusters_when_it_cannot_run(cannot_embed, monkeypatch):
    superseded = []

    async def _supersede(dep):
        superseded.append(dep)

    monkeypatch.setattr(embedding_lifecycle, "_supersede_complete_runs", _supersede)
    with pytest.raises(wildlife_brain.EmbeddingUnavailableError):
        await embedding_lifecycle.reprocess_deployment("d1")
    assert superseded == []


async def test_auto_embed_after_the_pipeline_skips_quietly(cannot_embed, monkeypatch):
    from app.jobs import definitions

    logged = []
    monkeypatch.setattr(definitions.logger, "info", lambda event, **kw: logged.append(("info", event, kw)))
    monkeypatch.setattr(definitions.logger, "warning", lambda event, **kw: logged.append(("warning", event, kw)))
    await definitions.auto_embed_deployment("d1", user_id="u1")  # must not raise
    assert [(lvl, ev) for lvl, ev, _ in logged] == [("info", "auto_embed_skipped")]
    assert "ML stack" in logged[0][2]["reason"]


async def test_auto_embed_is_a_no_op_with_the_flag_off(monkeypatch):
    from app.jobs import definitions

    monkeypatch.setattr(settings, "FF_WILDLIFE_BRAIN_ENABLED", False)
    monkeypatch.setattr(wildlife_brain, "ensure_embedding_available", lambda *a: pytest.fail("checked with the flag off"))
    await definitions.auto_embed_deployment("d1")


async def test_explicit_embed_job_fails_with_the_reason(cannot_embed, monkeypatch):
    from app.jobs import definitions

    updates = []

    async def _update(job_id, **kw):
        updates.append(kw)

    monkeypatch.setattr(definitions, "update_job", _update)
    await definitions.embed_deployment_job("job1", "d1")
    assert updates[-1]["status"] == definitions.JobStatus.FAILED
    assert "can't embed" in updates[-1]["error"]


# ── Routes, flag at its default ──────────────────────────────────────


@pytest.fixture
def brain_client(monkeypatch):
    """TestClient with auth bypassed and a service client whose every read is empty."""
    from fastapi.testclient import TestClient

    from app.authz import require_deployment_access, require_media_access
    from app.dependencies import get_current_user, get_verified_user
    from app.main import app
    from app.routers import brain
    from app.services import vector_store

    query = MagicMock()
    for name in ("select", "eq", "in_", "gte", "order", "limit", "maybe_single"):
        getattr(query, name).return_value = query
    query.execute.return_value = SimpleNamespace(data=None)
    svc = MagicMock()
    svc.table.return_value = query
    monkeypatch.setattr(brain, "create_service_client", lambda: svc)
    monkeypatch.setattr(vector_store, "create_service_client", lambda: svc)

    user = SimpleNamespace(id="u1", email="t@ww.ai")
    overrides = {
        get_current_user: lambda: user,
        get_verified_user: lambda: user,
        require_deployment_access: lambda: None,
        require_media_access: lambda: None,
    }
    app.dependency_overrides.update(overrides)
    yield TestClient(app)
    for dep in overrides:
        app.dependency_overrides.pop(dep, None)


def test_brain_routes_are_mounted_by_default(brain_client):
    paths = brain_client.get("/openapi.json").json()["paths"]
    assert "/api/brain/clusters/{deployment_id}" in paths
    assert "/api/brain/similar/{media_id}" in paths


def test_clusters_before_any_run_are_empty_not_an_error(brain_client):
    r = brain_client.get("/api/brain/clusters/d1")
    assert r.status_code == 200
    assert r.json()["data"] == {"embedding_run_id": None, "clusters": []}


def test_similar_without_an_embedding_is_not_found_not_a_500(brain_client):
    r = brain_client.get("/api/brain/similar/m1")
    assert r.status_code == 200
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_embed_route_queues_a_job(brain_client, monkeypatch):
    from app.jobs import dispatch, store

    queued = []

    async def _create_job(**kw):
        return "job1"

    async def _enqueue(name, *args, **kw):
        queued.append((name, args))
        return "local"

    monkeypatch.setattr(store, "create_job", _create_job)
    monkeypatch.setattr(dispatch, "enqueue_job", _enqueue)
    r = brain_client.post("/api/brain/embed/d1", json={"mode": "server"})
    assert r.status_code == 200
    assert r.json()["data"]["job_id"] == "job1"
    assert queued == [("embed_deployment_job", ("job1", "d1", None))]


def test_review_queue_with_active_learning_off_is_feature_disabled(brain_client, monkeypatch):
    monkeypatch.setattr(settings, "FF_ACTIVE_LEARNING_ENABLED", False)
    r = brain_client.get("/api/brain/review-queue/d1")
    assert r.status_code == 200
    assert r.json()["error"]["code"] == "FEATURE_DISABLED"
