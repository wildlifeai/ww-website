# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The CamtrapDP import as a job (#454): the route keeps or checks the package and answers a job,
the job reads the package from the object store, imports it, reports progress and its result."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from botocore.stub import Stubber
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from app.config import settings
from app.dependencies import get_current_user
from app.domain import camtrapdp as domain
from app.jobs import camtrapdp_import, dispatch, store
from app.routers import camtrapdp as router
from app.schemas.camtrapdp import CamtrapImportResult
from app.schemas.job import JobStatus
from app.services import incoming, lambda_invoke, object_store

USER = SimpleNamespace(id="u0000000-0000-4000-8000-000000000001", email="t@wildlife.ai", app_metadata={}, user_metadata={})
ORG = "o0000000-0000-4000-8000-000000000001"
JOB = "a0000000-0000-4000-8000-000000000001"


# ── a package ────────────────────────────────────────────────────────


def _csv(rows: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def _package(photos: int = 300, deployments=(("d1", "CAM1", "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"),), public: int = 0) -> bytes:
    """A CamtrapDP ZIP: ``photos`` images inside it, ``public`` more by URL, one observation each."""
    deps = [
        {"deploymentID": d, "cameraID": c, "deploymentStart": s, "deploymentEnd": e, "latitude": "-41.3", "longitude": "174.8"}
        for d, c, s, e in deployments
    ]
    media, obs = [], []
    for n in range(photos + public):
        dep = deps[n % len(deps)]["deploymentID"]
        path = f"media/IMG{n:04d}.JPG" if n < photos else f"https://example.org/IMG{n:04d}.JPG"
        media.append({"mediaID": f"m{n}", "deploymentID": dep, "filePath": path, "timestamp": "2026-01-02T03:04:05Z"})
        name = "Rattus rattus" if n % 2 else "Mareca strepera"
        obs.append({"observationID": f"o{n}", "deploymentID": dep, "mediaID": f"m{n}", "observationType": "animal", "scientificName": name})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("datapackage.json", json.dumps({"title": "Test package"}))
        zf.writestr("deployments.csv", _csv(deps))
        zf.writestr("media.csv", _csv(media))
        zf.writestr("observations.csv", _csv(obs))
        for n in range(photos):
            zf.writestr(f"media/IMG{n:04d}.JPG", b"\xff\xd8" + str(n).encode())
    return buf.getvalue()


# ── a service client over in-memory tables ───────────────────────────


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.filters, self.rows, self.values = db, name, [], None, None

    def select(self, _cols):
        return self

    def eq(self, col, val):
        self.filters.append(lambda r: r.get(col) == val)
        return self

    def in_(self, col, vals):
        self.filters.append(lambda r: r.get(col) in vals)
        return self

    def limit(self, _n):
        return self

    def insert(self, rows):
        self.rows = rows if isinstance(rows, list) else [rows]
        return self

    def upsert(self, rows, on_conflict=None):
        return self.insert(rows)

    def update(self, values):
        self.values = values
        return self

    def execute(self):
        table = self.db.tables.setdefault(self.name, [])
        if self.values is not None:
            hit = [r for r in table if all(f(r) for f in self.filters)]
            for r in hit:
                r.update(self.values)
            return SimpleNamespace(data=hit)
        if self.rows is None:
            return SimpleNamespace(data=[r for r in table if all(f(r) for f in self.filters)])
        for row in self.rows:
            if self.name == "deployments":
                if self.db.deployment_error:
                    raise self.db.deployment_error
                if not row.get("deployment_end") and any(d["device_id"] == row["device_id"] and not d.get("deployment_end") for d in table):
                    did = row["device_id"]
                    raise APIError({"code": "23P01", "message": "conflicting key value", "details": f"Key (device_id)=({did}) conflicts"})
            table.append(dict(row))
        return SimpleNamespace(data=self.rows)


class _Db:
    def __init__(self, deployment_error=None):
        self.tables: dict[str, list[dict]] = {"taxa": [{"scientific_name": "Mareca strepera"}]}
        self.deployment_error = deployment_error

    def table(self, name):
        return _Q(self, name)

    def rows(self, name):
        return self.tables.get(name, [])


class _Archive:
    """``upload_analysis_images`` as S3Archive answers it, without S3."""

    def __init__(self):
        self.files: list[dict] = []

    async def upload_analysis_images(self, files, file_callback=None):
        self.files.extend(files)
        out = []
        for n, f in enumerate(files, 1):
            key = f"originals/{f['project']['id']}/{f['deployment']['id']}/{n}.jpg"
            out.append({"file_id": key, "file_path": f"s3://bucket/{key}", "media_id": f["media_id"]})
            if file_callback:
                await file_callback(action="uploaded", filename=f["filename"], index=n, total=len(files))
        return {"uploaded": len(files), "skipped": 0, "failed": 0, "files": out}


# ── fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def local_store(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "OBJECT_STORE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_OBJECT_STORE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def jobs(monkeypatch):
    """The real job store, its Supabase mirror a mock."""
    monkeypatch.setattr(store, "create_service_client", lambda: MagicMock())
    store._memory_store.clear()
    store._memory_events.clear()
    yield store
    store._memory_store.clear()
    store._memory_events.clear()


@pytest.fixture
def importing(monkeypatch, local_store, jobs):
    """A job's world: a database, an S3 archive, iNaturalist and the AI flag off."""
    db = _Db()
    archive = _Archive()
    monkeypatch.setattr(camtrapdp_import, "create_service_client", lambda: db)
    monkeypatch.setattr(object_store, "archive_service", lambda: archive)
    monkeypatch.setattr(settings, "ARCHIVE_BACKEND", "s3")
    monkeypatch.setattr(settings, "S3_BUCKET", "bucket")
    monkeypatch.setattr(settings, "FF_ML_ENABLED", False)
    inat = AsyncMock(return_value={"scientific_name": "Rattus rattus", "inat_taxon_id": 44576})
    monkeypatch.setattr("app.domain.inaturalist.search_and_fetch_inat_taxon", inat)
    return SimpleNamespace(db=db, archive=archive, inat=inat)


async def _run(package: bytes | None, **kw) -> tuple[str, str]:
    """Keep ``package`` as the route does, run the job on it; returns ``(job_id, key)``."""
    key = await incoming.keep_for_job(USER.id, package) if package is not None else f"incoming/{USER.id}/gone/0-body"
    job_id = await store.create_job(user_id=USER.id, kind="camtrapdp_import")
    await camtrapdp_import.run_import(job_id, key, "pkg.zip", USER.id, ORG, kw.get("annotation_mode", "final"), kw.get("run_ai", False))
    return job_id, key


# ── the job ──────────────────────────────────────────────────────────


async def test_a_package_of_a_few_hundred_photos_completes_with_its_counts(importing):
    job_id, key = await _run(_package(photos=300, public=5))

    job = await store.get_job(job_id)
    assert job is not None and job.status == JobStatus.COMPLETED and job.progress == 1.0
    result = job.result or {}
    assert (result["deployments_imported"], result["media_imported"], result["observations_imported"]) == (1, 305, 305)
    assert result["project_name"] == "Test package" and result["warnings"] == [] and "pending_drive_uploads" not in result
    assert result["drive_uploads"] == {"uploaded": 300, "skipped": 0, "failed": 0}
    assert "305 photos" in (job.message or "")
    # The 300 images in the ZIP point at their archived copies; the 5 public URLs stay as they were.
    paths = [m["file_path"] for m in importing.db.rows("media")]
    assert sum(p.startswith("s3://bucket/originals/") for p in paths) == 300
    assert sum(p.startswith("https://example.org/") for p in paths) == 5
    # The missing taxon came from iNaturalist, the one the table had did not.
    importing.inat.assert_awaited_once_with("Rattus rattus")
    assert [t["scientific_name"] for t in importing.db.rows("taxa")] == ["Mareca strepera", "Rattus rattus"]
    # The package is gone from the object store.
    assert not await object_store.object_exists(key)


async def test_progress_is_written_while_the_job_runs(importing, monkeypatch):
    seen = []
    real = camtrapdp_import.update_job

    async def update_job(job_id, **kw):
        seen.append((kw.get("progress"), kw.get("message")))
        await real(job_id, **kw)

    monkeypatch.setattr(camtrapdp_import, "update_job", update_job)
    monkeypatch.setattr(camtrapdp_import, "REPORT_EVERY_SECONDS", 0.0)
    await _run(_package(photos=4))

    messages = [m for _p, m in seen if m]
    assert messages[0] == "Reading the package…"
    assert any("iNaturalist" in m for m in messages) and any(m.startswith("Importing 1 deployments") for m in messages)
    assert "Archived 4 of 4 photos…" in messages and messages[-1].startswith("Imported 1 deployments")
    progress = [p for p, _m in seen if p is not None]
    assert progress == sorted(progress)


async def test_a_second_open_deployment_still_imports_under_its_own_device(importing):
    """The gotcha "A camera has one open deployment, checked at commit" holds inside the job."""
    deps = (("d1", "CAM1", "2026-01-01T00:00:00Z", ""), ("d2", "CAM1", "2026-03-01T00:00:00Z", ""))
    job_id, _key = await _run(_package(photos=2, deployments=deps))

    job = await store.get_job(job_id)
    assert job is not None and job.status == JobStatus.COMPLETED_WITH_ERRORS
    assert (job.result or {})["deployments_imported"] == 2
    assert sorted(d["name"] for d in importing.db.rows("devices")) == ["[imported] CAM1", "[imported] CAM1 (d2)"]
    assert any("'CAM1'" in w and "'d2'" in w for w in (job.result or {})["warnings"])


@pytest.mark.parametrize(
    ("package", "error"),
    [
        (b"not a zip", "Uploaded file is not a valid ZIP archive."),
        (None, "The uploaded package is gone. Send it again."),
    ],
)
async def test_a_package_the_import_cannot_read_fails_the_job_with_the_reason(importing, package, error):
    job_id, key = await _run(package)

    job = await store.get_job(job_id)
    assert job is not None and job.status == JobStatus.FAILED and job.error == error
    assert importing.db.rows("projects") == [] and not await object_store.object_exists(key)


async def test_a_package_over_the_limit_fails_the_job(importing, monkeypatch):
    monkeypatch.setattr(domain, "MAX_UPLOAD_BYTES", 100)
    job_id, _key = await _run(_package(photos=2))

    job = await store.get_job(job_id)
    assert job is not None and job.status == JobStatus.FAILED and "limit" in (job.error or "")


async def test_a_package_over_an_unpacking_limit_fails_the_job_with_the_limit(importing):
    """The input limits of domain/zip_limits.py reach the user as the job's failure reason."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("datapackage.json", "{}")
        zf.writestr("observations.csv", bytes(2 * 1024 * 1024))
    job_id, _key = await _run(buf.getvalue())

    job = await store.get_job(job_id)
    assert job is not None and job.status == JobStatus.FAILED
    assert job.error == "'observations.csv' in the package is compressed more than 100 to 1, the limit for a file over 1 MB."
    assert importing.db.rows("projects") == []


async def test_a_database_error_fails_the_job_and_is_not_raised(importing):
    """A raise would count the invoke as failed, and a retried invoke would create the project again."""
    importing.db.deployment_error = APIError({"code": "23502", "message": "null value"})
    job_id, _key = await _run(_package(photos=2))

    job = await store.get_job(job_id)
    assert job is not None and job.status == JobStatus.FAILED
    assert (job.error or "").startswith("Import failed: Failed to insert deployment 'd1'")


async def test_run_ai_queues_the_pipeline_for_the_images_in_the_zip_only(importing, monkeypatch):
    monkeypatch.setattr(settings, "FF_ML_ENABLED", True)
    enqueue = AsyncMock(return_value="local")
    monkeypatch.setattr(dispatch, "enqueue_job", enqueue)
    job_id, _key = await _run(_package(photos=3, public=2), run_ai=True)

    job = await store.get_job(job_id)
    ai_job_id = (job.result or {})["ai_job_id"] if job else None
    assert ai_job_id
    name, queued_job, dep_ids, user_id, force, media_ids = enqueue.await_args.args
    assert (name, queued_job, user_id, force) == ("annotate_deployments_job", ai_job_id, USER.id, True)
    zip_media = {f["media_id"] for f in importing.archive.files}
    assert len(dep_ids) == 1 and set(media_ids) == zip_media and len(zip_media) == 3


def test_the_finished_job_keeps_a_hundred_warnings():
    result = CamtrapImportResult(
        project_id="p", project_name="n", deployments_imported=0, media_imported=0, observations_imported=0, warnings=[f"w{n}" for n in range(250)]
    )
    warnings = camtrapdp_import.result_of(result)["warnings"]
    assert len(warnings) == camtrapdp_import.MAX_RESULT_WARNINGS + 1 and warnings[-1] == "... and 150 more warning(s)."


# ── the route ────────────────────────────────────────────────────────


@pytest.fixture
def api(monkeypatch):
    from app.main import app

    queued = AsyncMock(return_value="local")
    monkeypatch.setattr(router, "project_creator_org_id", AsyncMock(return_value=ORG))
    monkeypatch.setattr(router, "create_job", AsyncMock(return_value=JOB))
    monkeypatch.setattr(router, "enqueue_job", queued)
    app.dependency_overrides[get_current_user] = lambda: USER
    try:
        yield SimpleNamespace(client=TestClient(app), queued=queued)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _post(api, files=None, data=None):
    return api.client.post("/api/camtrapdp/import", files=files, data=data or {}, headers={"Authorization": "Bearer t"})


def test_a_package_in_the_body_is_kept_for_the_job_and_the_route_answers_the_job(api, local_store):
    package = _package(photos=2)
    resp = _post(api, files={"file": ("pkg.zip", package, "application/zip")}, data={"annotation_mode": "unprocessed", "run_ai": "true"})

    assert resp.status_code == 200 and resp.json()["data"] == {"job_id": JOB, "status": "queued"}
    name, job_id, key, filename, user_id, org_id, mode, run_ai = api.queued.await_args.args
    assert (name, job_id, filename, user_id, org_id, mode, run_ai) == ("import_camtrapdp_job", JOB, "pkg.zip", USER.id, ORG, "unprocessed", True)
    assert key.startswith(f"incoming/{USER.id}/") and (local_store / key).read_bytes() == package


@pytest.fixture
def s3(monkeypatch):
    client = object_store.new_s3_client(region_name="ap-southeast-2", aws_access_key_id="testing", aws_secret_access_key="testing")
    monkeypatch.setattr(settings, "OBJECT_STORE_BACKEND", "s3")
    monkeypatch.setattr(settings, "S3_BUCKET", "ww-test-media")
    object_store.set_s3_client(client)
    stub = Stubber(client)
    with stub:
        yield stub
    object_store.set_s3_client(None)


def test_a_key_is_checked_but_not_read_and_handed_to_the_job(api, s3):
    """The Lambda request never holds the package: one HEAD, and the job reads it."""
    key = f"incoming/{USER.id}/up1/0-pkg.zip"
    s3.add_response("head_object", {"ContentLength": 10}, {"Bucket": "ww-test-media", "Key": key})
    resp = _post(api, data={"file_key": key})

    assert resp.status_code == 200
    assert api.queued.await_args.args[:4] == ("import_camtrapdp_job", JOB, key, "pkg.zip")
    s3.assert_no_pending_responses()


def test_a_key_with_nothing_behind_it_is_a_404(api, s3):
    s3.add_client_error("head_object", service_error_code="404", http_status_code=404)
    resp = _post(api, data={"file_key": f"incoming/{USER.id}/up1/0-pkg.zip"})
    assert resp.status_code == 404 and "pkg.zip" in resp.json()["detail"]
    api.queued.assert_not_awaited()


@pytest.mark.parametrize(
    ("form", "status"),
    [
        ({"files": {"file": ("pkg.txt", b"x", "text/plain")}}, 400),
        ({"files": {"file": ("pkg.zip", b"x", "application/zip")}, "data": {"file_key": "incoming/x/0-a.zip"}}, 400),
        ({"data": {"file_key": "incoming/someone-else/up1/0-pkg.zip"}}, 403),
    ],
)
def test_a_wrong_upload_is_refused_before_a_job_exists(api, s3, form, status):
    assert _post(api, files=form.get("files"), data=form.get("data")).status_code == status
    api.queued.assert_not_awaited()


def test_a_package_over_the_limit_is_refused(api, local_store, monkeypatch):
    monkeypatch.setattr(router, "MAX_UPLOAD_BYTES", 10)
    resp = _post(api, files={"file": ("pkg.zip", b"x" * 11, "application/zip")})
    assert resp.status_code == 413 and "limit" in resp.json()["detail"]


def test_a_caller_without_an_organisation_gets_403(api, local_store, monkeypatch):
    monkeypatch.setattr(router, "project_creator_org_id", AsyncMock(return_value=None))
    assert _post(api, files={"file": ("pkg.zip", b"x", "application/zip")}).status_code == 403
    api.queued.assert_not_awaited()


def test_no_object_store_answers_503(api, monkeypatch):
    monkeypatch.setattr(settings, "OBJECT_STORE_BACKEND", "azure")
    monkeypatch.setattr(settings, "AZURE_STORAGE_CONNECTION_STRING", "")
    assert _post(api, files={"file": ("pkg.zip", b"x", "application/zip")}).status_code == 503


def test_a_job_that_cannot_be_queued_is_failed_and_answers_503(api, local_store, monkeypatch):
    api.queued.side_effect = RuntimeError("invoke refused")
    update_job = AsyncMock()
    monkeypatch.setattr(router, "update_job", update_job)

    assert _post(api, files={"file": ("pkg.zip", b"x", "application/zip")}).status_code == 503
    assert update_job.await_args.kwargs["status"] == JobStatus.FAILED


# ── where the job runs ───────────────────────────────────────────────


async def test_on_lambda_the_job_goes_to_the_cpu_function(monkeypatch):
    """Never a task in the API process: it would stop when the invocation returns."""
    monkeypatch.setattr(settings, "WORKER_BACKEND", "lambda")
    monkeypatch.setattr(settings, "CPU_FUNCTION_NAME", "ww-dev-cpu")
    invoke = AsyncMock()
    monkeypatch.setattr(lambda_invoke, "invoke_job", invoke)
    local = []
    monkeypatch.setattr("app.jobs.runner.enqueue_local_job", lambda coro: local.append(coro.close()))
    args = (JOB, "incoming/u/1/0-body", "pkg.zip", USER.id, ORG, "final", False)

    assert await dispatch.enqueue_job("import_camtrapdp_job", *args) == "lambda"
    invoke.assert_awaited_once_with("import_camtrapdp_job", args, {})
    assert local == []


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("import_camtrapdp_job", (JOB, "k", "pkg.zip", USER.id, ORG)),
        ("export_camtrapdp_originals_job", (JOB, {"project_id": "p"}, {"user_token": "t"})),
    ],
)
async def test_on_azure_the_camtrapdp_jobs_stay_in_the_api_process(monkeypatch, name, args):
    """Never on the ARQ GPU worker, which would scale a T4 from zero for work with no model in it."""
    import arq

    monkeypatch.setattr(settings, "REDIS_URL", "redis://localhost:6379")
    pool = AsyncMock()
    monkeypatch.setattr(arq, "create_pool", pool)
    ran = []
    monkeypatch.setattr("app.jobs.runner.enqueue_local_job", lambda coro: ran.append(coro.close()))

    assert await dispatch.enqueue_job(name, *args) == "local"
    assert len(ran) == 1
    pool.assert_not_awaited()
