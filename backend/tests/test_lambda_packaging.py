# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Lambda glue: SSM bootstrap, the /events router, the blob handoff and the settings guard."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import aws_params, lambda_events
from app.config import Settings, settings
from app.services import blob_store, locks, object_store


@pytest.fixture(autouse=True)
def _local_store(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(locks, "_lease_client", lambda: None)
    monkeypatch.setattr(settings, "OBJECT_STORE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_OBJECT_STORE_DIR", str(tmp_path))


class FakeSsm:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get_parameters_by_path(self, **kwargs):
        self.calls.append(kwargs)
        return self.pages[len(self.calls) - 1]


def test_ssm_parameters_fill_the_environment_once_without_overriding(monkeypatch):
    monkeypatch.setenv("WW_SSM_PREFIX", "/ww/dev")
    monkeypatch.setenv("FF_ML_ENABLED", "pinned-by-template")
    monkeypatch.delenv("WW_TEST_SECRET", raising=False)
    monkeypatch.delenv("WW_TEST_OTHER", raising=False)
    ssm = FakeSsm(
        [
            {"Parameters": [{"Name": "/ww/dev/WW_TEST_SECRET", "Value": "s1"}, {"Name": "/ww/dev/FF_ML_ENABLED", "Value": "x"}], "NextToken": "t"},
            {"Parameters": [{"Name": "/ww/dev/WW_TEST_OTHER", "Value": "s2"}]},
        ]
    )
    import os

    try:
        assert aws_params.load_parameters_into_environ(ssm) == 2
        assert os.environ["WW_TEST_SECRET"] == "s1" and os.environ["WW_TEST_OTHER"] == "s2"
    finally:
        os.environ.pop("WW_TEST_SECRET", None)
        os.environ.pop("WW_TEST_OTHER", None)
    assert os.environ["FF_ML_ENABLED"] == "pinned-by-template"
    assert ssm.calls[0] == {"Path": "/ww/dev/", "Recursive": True, "WithDecryption": True}
    assert ssm.calls[1]["NextToken"] == "t"


def test_ssm_bootstrap_is_a_no_op_without_a_prefix(monkeypatch):
    monkeypatch.delenv("WW_SSM_PREFIX", raising=False)
    assert aws_params.load_parameters_into_environ(FakeSsm([])) == 0


def test_the_api_refuses_to_start_without_its_database_settings():
    bare = Settings(SUPABASE_URL="", SUPABASE_ANON_KEY="", SUPABASE_SERVICE_ROLE_KEY="")
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        bare.require_supabase()
    settings.require_supabase()  # the test environment has them


def test_a_retired_setting_left_in_ssm_or_a_dotenv_is_ignored(monkeypatch, tmp_path):
    """FF_CLUSTERING_ENABLED went with the image clustering routes (#453); old values must not stop startup."""
    monkeypatch.setenv("FF_CLUSTERING_ENABLED", "true")
    dotenv = tmp_path / ".env"
    dotenv.write_text("FF_CLUSTERING_ENABLED=true\n", encoding="utf-8")
    loaded = Settings(_env_file=dotenv)
    assert not hasattr(loaded, "FF_CLUSTERING_ENABLED")


# ── /events ──────────────────────────────────────────────────────────


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(lambda_events.router)
    return TestClient(app, raise_server_exceptions=False)


def test_the_cpu_function_runs_a_job_event(monkeypatch):
    monkeypatch.setattr(settings, "WW_LAMBDA_ROLE", "cpu")
    update = AsyncMock()
    monkeypatch.setattr("app.jobs.definitions.update_job", update)
    resp = _client().post("/events", json={"ww-job": "embeddings_backup_job", "args": ["job-1"], "kwargs": {}})
    assert resp.status_code == 200 and resp.json() == {"ran": "embeddings_backup_job"}
    assert update.await_args.args[0] == "job-1"


def test_a_spilled_payload_is_read_then_dropped(monkeypatch, tmp_path):
    import asyncio

    monkeypatch.setattr(settings, "WW_LAMBDA_ROLE", "cpu")
    update = AsyncMock()
    monkeypatch.setattr("app.jobs.definitions.update_job", update)
    asyncio.run(object_store.store_blob("payloads/p1.json", json.dumps({"args": ["job-2"], "kwargs": {}}).encode()))
    resp = _client().post("/events", json={"ww-job": "embeddings_backup_job", "payload_key": "payloads/p1.json"})
    assert resp.status_code == 200 and update.await_args.args[0] == "job-2"
    assert asyncio.run(object_store.retrieve_blob("payloads/p1.json")) == (None, None)


def test_jobs_are_refused_on_the_api_function_and_unknown_names_everywhere(monkeypatch):
    monkeypatch.setattr(settings, "WW_LAMBDA_ROLE", "api")
    assert _client().post("/events", json={"ww-job": "embeddings_backup_job", "args": ["j"]}).status_code == 403
    monkeypatch.setattr(settings, "WW_LAMBDA_ROLE", "cpu")
    assert _client().post("/events", json={"ww-job": "os_system", "args": []}).status_code == 400
    assert _client().post("/events", json={"something": "else"}).status_code == 400


def test_the_tick_reaps_and_reports_the_queue(monkeypatch, capsys):
    monkeypatch.setattr(settings, "WW_LAMBDA_ROLE", "api")
    monkeypatch.setattr(settings, "WORKER_BACKEND", "lambda")
    monkeypatch.setattr("app.jobs.store.reap_stale_jobs", AsyncMock(return_value=2))
    monkeypatch.setattr("app.domain.batch_lease.pending_summary", AsyncMock(return_value={"jobs": 3, "oldest_minutes": 42.5}))
    resp = _client().post("/events", json={"ww-tick": True})
    assert resp.json() == {"reaped": 2, "queued_jobs": 3, "oldest_minutes": 42.5}
    emf = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith('{"_aws"')]
    assert emf and emf[0]["BatchOldestQueuedMinutes"] == 42.5 and emf[0]["BatchQueuedJobs"] == 3
    assert emf[0]["_aws"]["CloudWatchMetrics"][0]["Namespace"] == "WildlifeWatcher"


# ── the conversion handoff ───────────────────────────────────────────


async def test_blob_store_stays_on_local_disk_by_default(tmp_path):
    await blob_store.store_blob("job-x", b"zip", metadata={"filename": "m.zip"})
    assert (blob_store.BLOB_DIR / "job-x.data").read_bytes() == b"zip"
    assert await blob_store.retrieve_blob("job-x") == (b"zip", {"filename": "m.zip"})
    await blob_store.delete_blob("job-x")
    assert not (tmp_path / "buffer").exists()


async def test_blob_store_hands_off_through_the_object_store_under_lambda(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "WORKER_BACKEND", "lambda")
    await blob_store.store_blob("job-y", b"zip", metadata={"filename": "m.zip"})
    assert (tmp_path / "buffer" / "handoff-job-y").read_bytes() == b"zip"
    assert await blob_store.retrieve_blob("job-y") == (b"zip", {"filename": "m.zip"})
    await blob_store.delete_blob("job-y")
    assert await blob_store.retrieve_blob("job-y") == (None, None)
