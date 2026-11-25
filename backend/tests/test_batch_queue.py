# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The REANNZ batch queue: dispatch records, the reaper's batch rules, leases and the worker routes."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.domain import batch_lease
from app.jobs import dispatch, store
from app.services import lambda_invoke, locks, object_store

REAL_PRESIGN_GET = object_store.presign_get

TOKEN = "cluster-token-for-tests"
TOKEN_SHA = hashlib.sha256(TOKEN.encode()).hexdigest()
JOB = "a0000000-0000-4000-8000-000000000001"
DEP = "d0000000-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def _no_shared_locks(monkeypatch):
    """No Redis and no database behind services.locks: the cross-process lock is a no-op here."""
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(locks, "_lease_client", lambda: None)


def _iso(minutes_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


# ── dispatch ─────────────────────────────────────────────────────────


async def test_lambda_backend_leaves_a_gpu_job_queued_with_a_dispatch_record(monkeypatch):
    monkeypatch.setattr(settings, "WORKER_BACKEND", "lambda")
    recorded = {}

    async def set_job_dispatch(job_id, spec):
        recorded[job_id] = spec
        return True

    monkeypatch.setattr(store, "set_job_dispatch", set_job_dispatch)
    mode = await dispatch.enqueue_job("annotate_deployments_job", JOB, [DEP], "u1", _defer_by=timedelta(seconds=60))

    assert mode == "batch"
    spec = recorded[JOB]
    assert spec["backend"] == "batch" and spec["kind"] == "annotate"
    assert spec["args"] == [[DEP], "u1"] and spec["kwargs"] == {} and spec["attempts"] == 0
    gap = datetime.fromisoformat(spec["not_before"]) - datetime.fromisoformat(spec["queued_at"])
    assert gap == timedelta(seconds=60)


async def test_lambda_backend_sends_cpu_jobs_to_the_cpu_function(monkeypatch):
    monkeypatch.setattr(settings, "WORKER_BACKEND", "lambda")
    monkeypatch.setattr(settings, "CPU_FUNCTION_NAME", "ww-dev-cpu")
    invoke = AsyncMock()
    monkeypatch.setattr(lambda_invoke, "invoke_job", invoke)
    assert await dispatch.enqueue_job("export_camtrapdp_originals_job", JOB, {"p": 1}, {"c": 2}) == "lambda"
    invoke.assert_awaited_once_with("export_camtrapdp_originals_job", (JOB, {"p": 1}, {"c": 2}), {})


async def test_lambda_thumbnail_backfill_goes_to_the_cpu_function(monkeypatch):
    """A background task would stop when the invocation returns, so the job is invoked instead."""
    from app.jobs import definitions

    monkeypatch.setattr(settings, "WORKER_BACKEND", "lambda")
    monkeypatch.setattr(settings, "CPU_FUNCTION_NAME", "ww-dev-cpu")
    monkeypatch.setattr(definitions, "create_job", AsyncMock(return_value=JOB))
    invoke = AsyncMock()
    monkeypatch.setattr(lambda_invoke, "invoke_job", invoke)
    local = []
    monkeypatch.setattr("app.jobs.runner.enqueue_local_job", lambda coro: local.append(coro.close()))
    dep = "d0000000-0000-0000-0000-000000000001"
    assert await definitions.start_thumbnail_backfill(dep, "u1", delay_s=20) == JOB
    invoke.assert_awaited_once_with("backfill_thumbnails_job", (JOB, dep), {})
    assert local == []
    assert dep not in definitions._pending_thumbnails


async def test_in_process_jobs_stay_in_process_even_with_redis(monkeypatch):
    """The five call sites that used enqueue_local_job keep running in the API process on Azure."""
    monkeypatch.setattr(settings, "REDIS_URL", "redis://localhost:6379")
    ran = []

    async def fake_job(*args):
        ran.append(args)

    monkeypatch.setattr("app.jobs.definitions.generate_manifest_job", fake_job)
    monkeypatch.setattr("app.jobs.runner.enqueue_local_job", lambda coro: coro.close())
    assert await dispatch.enqueue_job("generate_manifest_job", JOB, {"x": 1}) == "local"


async def test_invoke_job_inlines_small_payloads_and_spills_large_ones(monkeypatch):
    client = MagicMock()
    lambda_invoke.set_lambda_client(client)
    monkeypatch.setattr(settings, "CPU_FUNCTION_NAME", "ww-dev-cpu")
    stored = {}

    async def store_blob(key, data, metadata=None):
        stored[key] = data

    monkeypatch.setattr(object_store, "store_blob", store_blob)
    try:
        await lambda_invoke.invoke_job("convert_model_job", ["j", "u", "m"], {})
        event = json.loads(client.invoke.call_args.kwargs["Payload"])
        assert event == {"ww-job": "convert_model_job", "args": ["j", "u", "m"], "kwargs": {}}
        assert client.invoke.call_args.kwargs["InvocationType"] == "Event"

        await lambda_invoke.invoke_job("upload_drive_images_job", ["j", {"files": ["x" * 300_000]}], {})
        event = json.loads(client.invoke.call_args.kwargs["Payload"])
        assert event["payload_key"].startswith("payloads/") and "args" not in event
        assert event["payload_key"] in stored
    finally:
        lambda_invoke.set_lambda_client(None)


# ── the reaper's batch rules ─────────────────────────────────────────


def _jd(status="queued", minutes_ago=0, attempts=0, lease=None, backend="batch"):
    jd = {"status": status, "dispatch": {"backend": backend, "queued_at": _iso(minutes_ago), "attempts": attempts}}
    if lease:
        jd["lease"] = {"id": lease}
    return jd


def test_a_queued_batch_job_outlives_the_60_minute_rule():
    assert store.batch_reap_decision("queued", _jd(minutes_ago=180)) == ("keep", "")


def test_a_queued_batch_job_fails_after_the_queue_limit(monkeypatch):
    monkeypatch.setattr(settings, "BATCH_MAX_QUEUE_HOURS", 2)
    action, reason = store.batch_reap_decision("queued", _jd(minutes_ago=181))
    assert action == "fail" and "2 hours" in reason


@pytest.mark.parametrize(
    ("state", "attempts", "expected"),
    [("held", 0, "keep"), ("released", 0, None), ("expired", 0, "requeue"), ("expired", 1, "fail")],
)
def test_a_leased_job_follows_its_lease_row(monkeypatch, state, attempts, expected):
    monkeypatch.setattr(locks, "lease_state", lambda name: state)
    action, _ = store.batch_reap_decision("processing", _jd("processing", attempts=attempts, lease="L1"))
    assert action == expected


def test_jobs_outside_the_batch_keep_the_60_minute_rule():
    assert store.batch_reap_decision("queued", _jd(backend="lambda"))[0] is None
    assert store.batch_reap_decision("processing", {"status": "processing"})[0] is None


def test_requeued_job_data_uses_an_attempt_and_drops_the_lease():
    out = store.requeued_job_data(_jd("processing", attempts=0, lease="L1"), "Requeued")
    assert out["status"] == "queued" and "lease" not in out and out["dispatch"]["attempts"] == 1


async def test_reaper_requeues_an_expired_lease_and_spares_a_waiting_job(monkeypatch):
    rows = [
        {"id": "j-expired", "status": "processing", "job_data": _jd("processing", lease="L1"), "updated_at": _iso(5)},
        {"id": "j-waiting", "status": "queued", "job_data": _jd(minutes_ago=300), "updated_at": _iso(300)},
        {"id": "j-stalled", "status": "processing", "job_data": {"status": "processing", "updated_at": _iso(90)}, "updated_at": _iso(90)},
    ]
    client = MagicMock()
    client.table.return_value.select.return_value.in_.return_value.execute.return_value = SimpleNamespace(data=rows)
    monkeypatch.setattr(store, "create_service_client", lambda: client)
    monkeypatch.setattr(locks, "lease_state", lambda name: "expired")

    assert await store.reap_stale_jobs(60) == 1
    updates = [c.args[0] for c in client.table.return_value.update.call_args_list]
    by_status = {u["status"]: u for u in updates}
    assert by_status["queued"]["job_data"]["dispatch"]["attempts"] == 1
    assert by_status["failed"]["job_data"]["error"].startswith("Stalled")
    assert len(updates) == 2


# ── the lease row API on locks ───────────────────────────────────────


class _Rows:
    """Enough of PostgREST for the lease functions: insert, select, update, delete with eq and lt."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    def table(self, name):
        return _Q(self)


class _Q:
    def __init__(self, db):
        self.db, self.op, self.payload, self.preds = db, "select", None, []

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def select(self, *a, **k):
        return self

    def update(self, values):
        self.op, self.payload = "update", values
        return self

    def delete(self):
        self.op = "delete"
        return self

    def _get(self, row, column):
        if "->>" in column:
            col, key = column.split("->>")
            return str((row.get(col) or {}).get(key))
        return row.get(column)

    def eq(self, column, value):
        self.preds.append(lambda r: self._get(r, column) == value)
        return self

    def lt(self, column, value):
        self.preds.append(lambda r: self._get(r, column) < value)
        return self

    def execute(self):
        from postgrest.exceptions import APIError

        if self.op == "insert":
            if self.payload["id"] in self.db.rows:
                raise APIError({"code": "23505", "message": "duplicate"})
            self.db.rows[self.payload["id"]] = dict(self.payload)
            return SimpleNamespace(data=[self.payload])
        hit = [r for r in self.db.rows.values() if all(p(r) for p in self.preds)]
        for r in hit:
            if self.op == "update":
                r.update(self.payload)
            elif self.op == "delete":
                del self.db.rows[r["id"]]
        return SimpleNamespace(data=[dict(r) for r in hit])


def test_a_batch_lease_is_held_renewed_and_released(monkeypatch):
    db = _Rows()
    monkeypatch.setattr(locks, "_lease_client", lambda: db)
    assert locks.lease_state("batch-lease:L1") == "released"
    assert locks.acquire_lease("batch-lease:L1", "L1", 60) is True
    assert locks.acquire_lease("batch-lease:L1", "other", 60) is False
    assert locks.lease_state("batch-lease:L1") == "held"
    assert locks.renew_lease("batch-lease:L1", "L1", 60) is True
    assert locks.renew_lease("batch-lease:L1", "other", 60) is False
    locks.release_lease("batch-lease:L1", "L1")
    assert locks.lease_state("batch-lease:L1") == "released"


def test_an_expired_lease_reads_expired(monkeypatch):
    db = _Rows()
    monkeypatch.setattr(locks, "_lease_client", lambda: db)
    assert locks.acquire_lease("batch-lease:L2", "L2", -5)
    assert locks.lease_state("batch-lease:L2") == "expired"


# ── batch_lease ──────────────────────────────────────────────────────


def _queued_row(job_id=JOB, kind="annotate", minutes_ago=1, name="annotate_deployments_job", args=None):
    return {
        "id": job_id,
        "status": "queued",
        "created_at": _iso(minutes_ago),
        "job_data": {
            "dispatch": {
                "backend": "batch",
                "name": name,
                "kind": kind,
                "args": args if args is not None else [[DEP], "u1"],
                "kwargs": {},
                "queued_at": _iso(minutes_ago),
                "not_before": _iso(minutes_ago),
            }
        },
    }


@pytest.mark.parametrize(("photos", "minutes", "submit"), [(5, 1, False), (5, 61, True), (150, 1, True)])
async def test_pending_gate(monkeypatch, photos, minutes, submit):
    monkeypatch.setattr(batch_lease, "_batch_rows", lambda status: [_queued_row(minutes_ago=minutes)])
    monkeypatch.setattr(batch_lease, "_count_media", lambda deps: photos)
    summary = await batch_lease.pending_summary()
    assert summary["submit"] is submit and summary["jobs"] == 1 and summary["photos"] == photos
    assert 20 <= summary["time_limit_minutes"] <= 360


async def test_a_job_still_in_its_debounce_is_not_pending(monkeypatch):
    row = _queued_row()
    row["job_data"]["dispatch"]["not_before"] = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
    monkeypatch.setattr(batch_lease, "_batch_rows", lambda status: [row])
    assert (await batch_lease.pending_summary())["jobs"] == 0


@pytest.fixture
def leasing(monkeypatch, s3_folders):
    """claim() with the database, the lock and the pipeline replaced."""
    from app.schemas.pipeline import PipelineStepType

    monkeypatch.setattr(batch_lease, "_batch_rows", lambda status: [_queued_row()])
    monkeypatch.setattr("app.jobs.definitions.build_pipeline_steps", lambda: [PipelineStepType.SPECIESNET, PipelineStepType.BIOCLIP])
    monkeypatch.setattr(settings, "FF_BIOCLIP_ENABLED", True)
    s3_folders[DEP] = {"p"}
    media = [
        {"id": "m-s3", "deployment_id": DEP, "file_path": f"s3://ww-test-media/originals/p/{DEP}/h.jpg"},
        {"id": "m-drive", "deployment_id": DEP, "file_path": "gdrive://abc"},
    ]
    monkeypatch.setattr("app.domain.pipeline.fetch_pipeline_media", lambda svc, dep, steps, **kw: media)
    monkeypatch.setattr(batch_lease, "create_service_client", lambda: MagicMock())
    monkeypatch.setattr(locks, "acquire_lease", lambda name, holder, ttl: True)
    monkeypatch.setattr(locks, "release_lease", lambda name, holder: None)
    patches = []

    async def patch_job_data(job_id, patch, status=None):
        patches.append((job_id, patch, status))
        return patch

    monkeypatch.setattr(batch_lease, "patch_job_data", patch_job_data)
    monkeypatch.setattr(object_store, "presign_get", lambda key, ttl=None, bucket=None, filename=None: f"https://s3/{bucket}/{key}?ttl={ttl}")
    return patches


async def test_claim_builds_a_manifest_and_marks_the_job_processing(leasing):
    manifest = await batch_lease.claim(slurm_job_id="12345")
    lease_id = manifest["lease_id"]
    job = manifest["jobs"][0]
    assert job["job_id"] == JOB and job["kind"] == "annotate"
    assert job["models"] == {"speciesnet": True, "bioclip": True}
    assert job["config"]["confidence_threshold"] == 0.2 and "bioclip_rank" in job["config"]
    s3_item, drive_item = job["items"]
    assert s3_item["url"].startswith(f"https://s3/ww-test-media/originals/p/{DEP}/h.jpg")
    # Never a raw user-writable URL: everything not in S3 goes through the API's resolver.
    assert drive_item == {"media_id": "m-drive", "deployment_id": DEP, "path": f"/api/worker/lease/{lease_id}/media/m-drive"}
    assert "items_total" not in job  # a cluster that does not ask for pages gets today's manifest
    (job_id, patch, status), *_ = leasing
    assert job_id == JOB and status == store.JobStatus.PROCESSING and patch["lease"]["id"] == lease_id
    assert patch["lease"]["slurm_job_id"] == "12345"


async def test_claim_completes_a_job_with_nothing_left(leasing, monkeypatch):
    monkeypatch.setattr("app.domain.pipeline.fetch_pipeline_media", lambda svc, dep, steps, **kw: [])
    assert await batch_lease.claim() is None
    (_, patch, status), *_ = leasing
    assert status == store.JobStatus.COMPLETED and "No new photos" in patch["message"]


async def test_claim_returns_none_when_the_queue_is_empty(monkeypatch):
    monkeypatch.setattr(batch_lease, "_batch_rows", lambda status: [])
    assert await batch_lease.claim() is None


# A Lambda role's session token, about as long as AWS issues, goes into every presigned GET.
SESSION_TOKEN = "IQoJb3JpZ2luX2VjE" + "A" * 1100
LAMBDA_ANSWER_MAX = 6 * 1024 * 1024


def _lambda_answer_bytes(payload) -> int:
    """Bytes of a JSON answer once the Lambda Web Adapter wraps it as a string, quotes escaped."""
    return len(json.dumps(json.dumps(payload, separators=(",", ":"))))


@pytest.fixture
def lambda_presign(monkeypatch):
    """presign_get as on Lambda: a real SigV4 URL from temporary credentials, offline."""
    monkeypatch.setattr(object_store, "presign_get", REAL_PRESIGN_GET)
    client = object_store.new_s3_client(
        region_name="ap-southeast-6", aws_access_key_id="ASIATESTKEY", aws_secret_access_key="testing", aws_session_token=SESSION_TOKEN
    )
    object_store.set_s3_client(client)
    yield
    object_store.set_s3_client(None)


def _leased_as(lease_id: str) -> list[dict]:
    return [{"id": JOB, "status": "processing", "job_data": {"lease": {"id": lease_id}, "dispatch": _queued_row()["job_data"]["dispatch"]}}]


async def test_a_3000_photo_lease_fits_lambdas_answer_when_paged(leasing, lambda_presign, monkeypatch, tmp_path):
    """#457: the manifest names the job's size only; each page of signed URLs stays far under 6 MB."""
    media = [
        {"id": f"m{n:05d}", "deployment_id": DEP, "file_path": f"s3://ww-test-media/originals/p/{DEP}/{n:064x}.jpg", "file_name": "x.jpg"}
        for n in range(3000)
    ]
    monkeypatch.setattr("app.domain.pipeline.fetch_pipeline_media", lambda svc, dep, steps, **kw: media)
    monkeypatch.setattr(settings, "OBJECT_STORE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_OBJECT_STORE_DIR", str(tmp_path))

    manifest = await batch_lease.claim(paged=True)
    (job,) = manifest["jobs"]
    assert "items" not in job and job["items_total"] == 3000
    assert _lambda_answer_bytes(manifest) < 10_000
    (_, patch, _), *_ = leasing
    assert patch["message"] == "Running on the GPU cluster (3000 photos)"

    monkeypatch.setattr(batch_lease, "_lease_rows", _leased_as)
    seen: list[str] = []
    offset = 0
    while offset < job["items_total"]:
        page = await batch_lease.items_page(manifest["lease_id"], JOB, offset, limit=5000)
        assert page["total"] == 3000 and len(page["items"]) == batch_lease.ITEMS_PAGE_MAX
        assert _lambda_answer_bytes(page) < LAMBDA_ANSWER_MAX / 2
        assert "X-Amz-Security-Token" in page["items"][0]["url"]
        seen.extend(i["media_id"] for i in page["items"])
        offset += len(page["items"])
    assert seen == [m["id"] for m in media]


async def test_items_page_needs_a_paged_job_of_the_lease(leased, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "OBJECT_STORE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_OBJECT_STORE_DIR", str(tmp_path))
    with pytest.raises(batch_lease.LeaseError):
        await batch_lease.items_page("L1", JOB)  # leased with the photos inline
    with pytest.raises(batch_lease.LeaseError):
        await batch_lease.items_page("L1", "someone-elses-job")
    await object_store.put_object(
        batch_lease.items_key("L1", JOB), json.dumps([{"id": "m1", "deployment_id": DEP, "file_path": "gdrive://a"}]).encode()
    )
    page = await batch_lease.items_page("L1", JOB, 0, 10)
    assert page == {"items": [{"media_id": "m1", "deployment_id": DEP, "path": "/api/worker/lease/L1/media/m1"}], "offset": 0, "total": 1}


async def test_embed_leases_hand_out_public_crops_and_proxy_the_rest(monkeypatch):
    crops = [
        {"id": "m1", "deployment_id": DEP, "crop_url": f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1/object/public/media-renditions/c1.jpg"},
        {"id": "m2", "deployment_id": DEP, "crop_url": "https://example.org/c2.jpg"},
    ]
    monkeypatch.setattr("app.domain.wildlife_brain._fetch_crops", AsyncMock(return_value=crops))
    work = await batch_lease._embed_work({"name": "embed_deployment_job", "args": [DEP, None]})
    assert work["models"] == {"dinov3": settings.EMBEDDING_DEFAULT_MODEL}
    items = await batch_lease._items("L1", "embed", work["sources"])
    assert items[0]["url"].endswith("/c1.jpg")
    assert items[1] == {"media_id": "m2", "deployment_id": DEP, "path": "/api/worker/lease/L1/media/m2?crop=1"}


@pytest.fixture
def leased(monkeypatch):
    rows = [{"id": JOB, "status": "processing", "job_data": {"lease": {"id": "L1"}, "dispatch": _queued_row()["job_data"]["dispatch"]}}]
    monkeypatch.setattr(batch_lease, "_lease_rows", lambda lease_id: rows if lease_id == "L1" else [])
    return rows


async def test_part_target_is_a_presigned_put_on_s3_and_the_api_otherwise(leased, monkeypatch):
    monkeypatch.setattr(settings, "OBJECT_STORE_BACKEND", "s3")
    monkeypatch.setattr(object_store, "presign_put", lambda key, ttl, ct: f"https://s3/{key}")
    target = await batch_lease.part_target("L1", JOB, 3)
    assert target["url"] == f"https://s3/results/L1/{JOB}/part-0003.jsonl.gz"
    monkeypatch.setattr(settings, "OBJECT_STORE_BACKEND", "local")
    assert (await batch_lease.part_target("L1", JOB, 3))["path"] == f"/api/worker/lease/L1/part/{JOB}/3"


async def test_part_target_refuses_a_job_outside_the_lease(leased):
    with pytest.raises(batch_lease.LeaseError):
        await batch_lease.part_target("L1", "someone-elses-job", 1)
    with pytest.raises(batch_lease.LeaseError):
        await batch_lease.part_target("L-unknown", JOB, 1)


async def test_part_complete_starts_ingest_only_for_an_uploaded_part(leased, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "OBJECT_STORE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_OBJECT_STORE_DIR", str(tmp_path))
    enqueue = AsyncMock()
    monkeypatch.setattr(dispatch, "enqueue_job", enqueue)
    with pytest.raises(batch_lease.LeaseError):
        await batch_lease.part_complete("L1", JOB, 1)
    await batch_lease.store_part("L1", JOB, 1, b"gz")
    await batch_lease.part_complete("L1", JOB, 1)
    enqueue.assert_awaited_once_with("ingest_results_part_job", "L1", JOB, 1)


async def test_finish_records_part_counts_releases_and_finalises(leased, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "OBJECT_STORE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_OBJECT_STORE_DIR", str(tmp_path))
    released = []
    monkeypatch.setattr(locks, "release_lease", lambda name, holder: released.append(name))
    enqueue = AsyncMock()
    monkeypatch.setattr(dispatch, "enqueue_job", enqueue)
    await batch_lease.finish("L1", [{"job_id": JOB, "parts_total": 2, "failed_media": ["m9"]}])
    done = json.loads((tmp_path / "results" / "L1" / JOB / "_done.json").read_text())
    assert done == {"parts_total": 2, "failed_media": ["m9"]}
    assert released == ["batch-lease:L1"]
    enqueue.assert_awaited_once_with("finalize_batch_job", "L1", JOB)


async def test_media_bytes_refuses_media_outside_the_leased_deployments(leased, monkeypatch):
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.execute.return_value = SimpleNamespace(
        data=[{"id": "m1", "deployment_id": "another-deployment", "file_path": "gdrive://x"}]
    )
    monkeypatch.setattr(batch_lease, "create_service_client", lambda: client)
    with pytest.raises(batch_lease.LeaseError):
        await batch_lease.media_served("L1", "m1")


async def test_heartbeat_renews_or_cancels(leased, monkeypatch):
    monkeypatch.setattr(locks, "renew_lease", lambda name, holder, ttl: False)
    assert await batch_lease.heartbeat("L1", 1, 2) == {"cancel": True}
    monkeypatch.setattr(locks, "renew_lease", lambda name, holder, ttl: True)
    patches = []

    async def patch_job_data(job_id, patch, status=None):
        patches.append(patch)

    monkeypatch.setattr(batch_lease, "patch_job_data", patch_job_data)
    out = await batch_lease.heartbeat("L1", 50, 100)
    assert out["cancel"] is False and patches[0]["message"] == "GPU cluster: 50 of 100 photos"


# ── the worker router ────────────────────────────────────────────────


def test_worker_routes_are_inert_without_a_token_hash(monkeypatch):
    from app.main import app

    monkeypatch.setattr(settings, "WORKER_TOKEN_SHA256", "")
    assert TestClient(app).get("/api/worker/pending", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 404


def test_worker_routes_check_the_bearer_token(monkeypatch):
    from app.main import app

    monkeypatch.setattr(settings, "WORKER_TOKEN_SHA256", TOKEN_SHA)
    monkeypatch.setattr(batch_lease, "pending_summary", AsyncMock(return_value={"submit": False}))
    client = TestClient(app)
    assert client.get("/api/worker/pending").status_code == 401
    assert client.get("/api/worker/pending", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/api/worker/pending", headers={"Authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 200 and ok.json() == {"submit": False}


def test_worker_routes_are_not_in_the_openapi_document():
    from app.main import app

    assert not any(p.startswith("/api/worker") for p in app.openapi()["paths"])


def test_lease_route_maps_an_empty_queue_and_lease_errors(monkeypatch):
    from app.main import app

    monkeypatch.setattr(settings, "WORKER_TOKEN_SHA256", TOKEN_SHA)
    monkeypatch.setattr(batch_lease, "claim", AsyncMock(return_value=None))
    monkeypatch.setattr(batch_lease, "part_target", AsyncMock(side_effect=batch_lease.LeaseError("gone")))
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert client.post("/api/worker/lease", json={}, headers=auth).json() == {"lease_id": None}
    assert client.post("/api/worker/lease/L1/part", json={"job_id": JOB, "part": 1}, headers=auth).status_code == 409


def test_lease_route_asks_for_pages_only_when_the_cluster_does(monkeypatch):
    from app.main import app

    monkeypatch.setattr(settings, "WORKER_TOKEN_SHA256", TOKEN_SHA)
    claim = AsyncMock(return_value=None)
    monkeypatch.setattr(batch_lease, "claim", claim)
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    client.post("/api/worker/lease", json={"slurm_job_id": "7"}, headers=auth)  # an image from before paging
    client.post("/api/worker/lease", json={"slurm_job_id": "8", "paged": True}, headers=auth)
    assert [c.kwargs["paged"] for c in claim.await_args_list] == [False, True]


def test_items_route_serves_pages_and_maps_lease_errors(monkeypatch):
    from app.main import app

    monkeypatch.setattr(settings, "WORKER_TOKEN_SHA256", TOKEN_SHA)
    page = AsyncMock(return_value={"items": [], "offset": 500, "total": 600})
    monkeypatch.setattr(batch_lease, "items_page", page)
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    resp = client.get(f"/api/worker/lease/L1/items/{JOB}", params={"offset": 500, "limit": 500}, headers=auth)
    assert resp.json() == {"items": [], "offset": 500, "total": 600}
    page.assert_awaited_once_with("L1", JOB, 500, 500)
    page.side_effect = batch_lease.LeaseError("gone")
    assert client.get(f"/api/worker/lease/L1/items/{JOB}", headers=auth).status_code == 409


def test_lease_media_route_redirects_a_large_original_and_answers_a_small_one(monkeypatch):
    from app.domain.media_resolver import ServedMedia
    from app.main import app

    monkeypatch.setattr(settings, "WORKER_TOKEN_SHA256", TOKEN_SHA)
    served = AsyncMock(return_value=ServedMedia(url="https://s3/buffer/media/abc?sig"))
    monkeypatch.setattr(batch_lease, "media_served", served)
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    big = client.get("/api/worker/lease/L1/media/m1", headers=auth, follow_redirects=False)
    assert big.status_code == 307 and big.headers["location"] == "https://s3/buffer/media/abc?sig"
    served.return_value = ServedMedia(data=b"jpeg", content_type="image/jpeg")
    small = client.get("/api/worker/lease/L1/media/m1", headers=auth)
    assert small.status_code == 200 and small.content == b"jpeg"
