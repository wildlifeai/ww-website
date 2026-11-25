# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The CamtrapDP export with the original photos (#328): domain, job, routes and the signed link (#462).

Fakes stand in for ww-backend's export-camtrap-dp (a metadata ZIP shaped like its package.ts
output), for Drive (a fetch function) and for Azure Blob Storage. S3 is a real boto3 client with
dummy credentials: presigning is offline.
"""

import csv
import io
import json
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

import pytest
from fastapi import HTTPException

from app.domain import camtrapdp_export as export
from app.services import camtrap_dp_function as fn

PROJECT = "11111111-1111-4111-8111-111111111111"
DEP_A = "aaaaaaaa-0000-4000-8000-000000000001"
DEP_B = "bbbbbbbb-0000-4000-8000-000000000002"


def _mid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


MEDIA_FIELDS = ["mediaID", "deploymentID", "captureMethod", "timestamp", "filePath", "filePublic", "fileName", "fileMediatype"]


def _media_rows(spec):
    """spec: (media number, deployment id, ext, filePublic)."""
    return [
        {
            "mediaID": _mid(n),
            "deploymentID": dep,
            "captureMethod": "activityDetection",
            "timestamp": "2026-01-01T00:00:00Z",
            "filePath": f"media/{dep}/{_mid(n)}.{ext}",
            "filePublic": str(public).lower(),
            "fileName": f"IMG_{n}.{ext.upper()}",
            "fileMediatype": "image/jpeg",
        }
        for n, dep, ext, public in spec
    ]


def _metadata_zip(path: Path, rows: list[dict], description: str = "Project notes") -> Path:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=MEDIA_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("datapackage.json", json.dumps({"name": "p", "description": description, "resources": []}))
        z.writestr("deployments.csv", f"deploymentID\n{DEP_A}\n{DEP_B}\n")
        z.writestr("media.csv", buf.getvalue())
        z.writestr("observations.csv", "observationID,mediaID\n")
    return path


def _originals(rows, prefix="gdrive://"):
    return {r["mediaID"]: export.Original(f"{prefix}{r['mediaID']}", 10) for r in rows}


class FakeDrive:
    """Fetch by file path. ``fail`` maps a path to how many reads fail before one works (None: all)."""

    def __init__(self, fail=None):
        self.fail = dict(fail or {})
        self.calls: list[str] = []

    async def __call__(self, file_path: str):
        self.calls.append(file_path)
        left = self.fail.get(file_path, 0)
        if left is None:
            return None
        if left:
            self.fail[file_path] = left - 1
            raise RuntimeError("drive 503")
        return f"JPEG:{file_path}".encode()


# ── read_photos: the media.csv contract ───────────────────────────────────


def test_read_photos_keeps_contract_paths_and_reports_the_rest(tmp_path):
    rows = _media_rows([(1, DEP_A, "jpg", True), (2, DEP_B, "png", False)])
    rows.append({**_media_rows([(3, DEP_A, "jpg", True)])[0], "filePath": "media/../../etc/passwd"})
    rows.append({**_media_rows([(4, DEP_A, "jpg", True)])[0], "filePath": f"media/{DEP_B}/{_mid(4)}.jpg"})  # wrong deployment
    with zipfile.ZipFile(_metadata_zip(tmp_path / "m.zip", rows)) as meta:
        photos, bad = export.read_photos(meta)
    assert [(p.media_id, p.path) for p in photos] == [(_mid(1), f"media/{DEP_A}/{_mid(1)}.jpg"), (_mid(2), f"media/{DEP_B}/{_mid(2)}.png")]
    assert [m for m, _ in bad] == [_mid(3), _mid(4)]


def test_read_photos_needs_media_csv(tmp_path):
    with zipfile.ZipFile(tmp_path / "m.zip", "w") as z:
        z.writestr("datapackage.json", "{}")
    with zipfile.ZipFile(tmp_path / "m.zip") as meta, pytest.raises(export.ExportError, match="media.csv"):
        export.read_photos(meta)


# ── write_package: layout, missing originals, the cap ─────────────────────


async def test_every_original_lands_at_its_media_csv_path(tmp_path):
    rows = _media_rows([(1, DEP_A, "jpg", True), (2, DEP_A, "jpg", False), (3, DEP_B, "png", True)])
    meta_path = _metadata_zip(tmp_path / "m.zip", rows)
    with zipfile.ZipFile(meta_path) as meta:
        photos, _ = export.read_photos(meta)
        meta_files = {n: meta.read(n) for n in meta.namelist()}
    drive = FakeDrive()
    progress = []

    async def on_progress(done, total, failed):
        progress.append((done, total, failed))

    result = await export.write_package(
        meta_path, tmp_path / "out.zip", photos, _originals(rows), fetch=drive, max_bytes=10**9, on_progress=on_progress, backoff_s=0
    )

    with zipfile.ZipFile(tmp_path / "out.zip") as out:
        media_csv = list(csv.DictReader(io.StringIO(out.read("media.csv").decode())))
        for row in media_csv:
            assert out.read(row["filePath"]) == f"JPEG:gdrive://{row['mediaID']}".encode()
            assert out.getinfo(row["filePath"]).compress_type == zipfile.ZIP_STORED
        # The metadata is the function's, unchanged: filePublic included.
        for name in ("media.csv", "deployments.csv", "observations.csv"):
            assert out.read(name) == meta_files[name]
        assert [r["filePublic"] for r in media_csv] == ["true", "false", "true"]
        assert json.loads(out.read("datapackage.json"))["description"] == "Project notes"
        assert sorted(n for n in out.namelist() if n.startswith("media/")) == sorted(r["filePath"] for r in rows)
    assert (result.photos, result.written, result.missing) == (3, 3, [])
    assert progress[-1] == (3, 3, 0)


async def test_missing_originals_are_retried_reported_and_not_fatal(tmp_path):
    rows = _media_rows([(1, DEP_A, "jpg", True), (2, DEP_A, "jpg", True), (3, DEP_A, "jpg", True), (4, DEP_B, "jpg", True)])
    meta_path = _metadata_zip(tmp_path / "m.zip", rows)
    with zipfile.ZipFile(meta_path) as meta:
        photos, _ = export.read_photos(meta)
    originals = _originals(rows)
    originals[_mid(3)] = export.Original("media/IMG_3.JPG")  # imported without its file: never fetched
    del originals[_mid(4)]  # deleted since the function ran
    drive = FakeDrive(fail={f"gdrive://{_mid(1)}": 2, f"gdrive://{_mid(2)}": None})

    result = await export.write_package(
        meta_path, tmp_path / "out.zip", photos, originals, fetch=drive, max_bytes=10**9, missing=[("bad-id", "unexpected filePath")], backoff_s=0
    )

    assert drive.calls.count(f"gdrive://{_mid(1)}") == 3  # two failures, then it reads
    assert drive.calls.count(f"gdrive://{_mid(2)}") == export.FETCH_ATTEMPTS
    assert "media/IMG_3.JPG" not in drive.calls
    assert result.written == 1 and result.photos == 5
    assert dict(result.missing) == {
        "bad-id": "unexpected filePath",
        _mid(2): "could not be read from storage",
        _mid(3): "no stored original",
        _mid(4): "not found in the database",
    }
    with zipfile.ZipFile(tmp_path / "out.zip") as out:
        description = json.loads(out.read("datapackage.json"))["description"]
        assert out.namelist().count(f"media/{DEP_A}/{_mid(1)}.jpg") == 1
        assert f"media/{DEP_A}/{_mid(2)}.jpg" not in out.namelist()
        assert len(list(csv.DictReader(io.StringIO(out.read("media.csv").decode())))) == 4  # still listed
    assert description.startswith("Project notes\n\nOriginal photo not included: 4 photo(s)")
    assert _mid(2) in description and _mid(4) in description


def test_note_missing_names_twenty_and_counts_the_rest():
    missing = [(_mid(n), "x") for n in range(25)]
    text = export.note_missing({"description": ""}, missing)["description"]
    assert text.startswith("Original photo not included: 25 photo(s)")
    assert _mid(19) in text and _mid(20) not in text and text.endswith("and 5 others.")


async def test_the_cap_stops_a_package_that_outgrows_it(tmp_path):
    rows = _media_rows([(n, DEP_A, "jpg", True) for n in range(1, 6)])
    meta_path = _metadata_zip(tmp_path / "m.zip", rows)
    with zipfile.ZipFile(meta_path) as meta:
        photos, _ = export.read_photos(meta)
    one = len(f"JPEG:gdrive://{_mid(1)}")
    with pytest.raises(export.ExportTooLarge, match="Export fewer deployments"):
        await export.write_package(meta_path, tmp_path / "out.zip", photos, _originals(rows), fetch=FakeDrive(), max_bytes=one * 2, concurrency=1)


def test_estimate_uses_recorded_sizes_and_their_average():
    photos = [export.Photo(_mid(n), DEP_A, "p") for n in range(4)]
    originals = {_mid(0): export.Original("gdrive://a", 100), _mid(1): export.Original("gdrive://b", 300), _mid(2): export.Original("gdrive://c")}
    assert export.estimate_bytes(photos, originals) == 800
    assert export.estimate_bytes(photos[:1], {}) == export.ASSUMED_PHOTO_BYTES
    export.check_size(800, 800)
    with pytest.raises(export.ExportTooLarge):
        export.check_size(801, 800)


# ── lookup_originals: every row past PostgREST's 1,000 ────────────────────


class FakeMediaTable:
    def __init__(self, rows):
        self.rows, self.filters, self.after, self.limit_n = rows, {}, None, None

    def select(self, *_):
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def is_(self, *_):
        return self

    def gt(self, _col, val):
        self.after = val
        return self

    def order(self, *_):
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def execute(self):
        rows = sorted((r for r in self.rows if r["deployment_id"] == self.filters["deployment_id"]), key=lambda r: r["id"])
        rows = [r for r in rows if self.after is None or r["id"] > self.after][: min(self.limit_n, 1000)]
        return SimpleNamespace(data=rows)


class FakeSvc:
    def __init__(self, media_rows):
        self.media_rows = media_rows
        self.other = MagicMock()

    def table(self, name):
        return FakeMediaTable(self.media_rows) if name == "media" else self.other.table(name)


def test_lookup_reads_every_page():
    db = [{"id": _mid(n), "deployment_id": DEP_A, "file_path": f"gdrive://{n}", "media_assets": {"file_size_bytes": n}} for n in range(2500)]
    db.append({"id": _mid(9999), "deployment_id": DEP_A, "file_path": "gdrive://x", "media_assets": None})
    photos = [export.Photo(r["id"], DEP_A, "p") for r in db]
    found = export.lookup_originals(FakeSvc(db), photos)
    assert len(found) == 2501
    assert found[_mid(2400)] == export.Original("gdrive://2400", 2400)
    assert found[_mid(9999)] == export.Original("gdrive://x", None)


# ── build_export and the function: authorisation is the function's ────────


def _fake_function(rows, calls, status=200, message=""):
    async def download(body, dest, *, user_token):
        calls.append((body, user_token))
        if status != 200:
            raise fn.CamtrapFunctionError(status, message)
        _metadata_zip(dest, rows)

    return download


async def _noop(*_a, **_k):
    return None


async def test_build_export_calls_the_function_as_the_user(tmp_path, monkeypatch):
    rows = _media_rows([(1, DEP_A, "jpg", True)])
    calls = []
    monkeypatch.setattr(fn, "download_package", _fake_function(rows, calls))
    monkeypatch.setattr(
        "app.services.supabase_client.create_service_client", lambda: FakeSvc([{"id": _mid(1), "deployment_id": DEP_A, "file_path": "gdrive://1"}])
    )
    sel = export.ExportSelection(PROJECT, [DEP_A], "2026-01-01T00:00:00+00:00", None)

    zip_path, result = await export.build_export(
        sel, export.ExportCaller(user_token="user-jwt"), tmp_path, max_bytes=10**9, on_message=_noop, fetch=FakeDrive()
    )

    assert calls == [({"project_id": PROJECT, "deployment_ids": [DEP_A], "from": "2026-01-01T00:00:00+00:00"}, "user-jwt")]
    assert result.written == 1 and zip_path.exists() and not (tmp_path / "metadata.zip").exists()


async def test_an_export_of_s3_originals_has_every_photo_in_media(tmp_path, monkeypatch):
    """With ARCHIVE_BACKEND=s3 (AWS) originals are s3:// keys, and each one is read into media/ (#493)."""
    rows = _media_rows([(1, DEP_A, "jpg", True), (2, DEP_A, "jpg", False), (3, DEP_A, "png", True)])
    keys = {r["mediaID"]: f"s3://ww-test-media/originals/{PROJECT}/{DEP_A}/{r['mediaID']}.jpg" for r in rows}
    monkeypatch.setattr(fn, "download_package", _fake_function(rows, []))
    media = [{"id": m, "deployment_id": DEP_A, "file_path": key} for m, key in keys.items()]
    monkeypatch.setattr("app.services.supabase_client.create_service_client", lambda: FakeSvc(media))
    fetch = FakeDrive()

    zip_path, result = await export.build_export(
        export.ExportSelection(PROJECT, [DEP_A]), export.ExportCaller(user_token="t"), tmp_path, max_bytes=10**9, on_message=_noop, fetch=fetch
    )

    assert sorted(fetch.calls) == sorted(keys.values())
    assert (result.photos, result.written, result.missing) == (3, 3, [])
    with zipfile.ZipFile(zip_path) as out:
        for row in rows:
            assert out.read(row["filePath"]) == f"JPEG:{keys[row['mediaID']]}".encode()
        assert json.loads(out.read("datapackage.json"))["description"] == "Project notes"


@pytest.mark.parametrize(
    ("status", "message", "shown"),
    [
        (403, "Not a member of this project.", "Not a member of this project."),
        (413, "Dataset too large: 60000 media rows", "Dataset too large"),
        (401, "Invalid or expired token.", "sign-in expired"),
    ],
)
async def test_the_functions_refusal_fails_the_export(tmp_path, monkeypatch, status, message, shown):
    monkeypatch.setattr(fn, "download_package", _fake_function([], [], status, message))
    with pytest.raises(export.ExportError, match=shown):
        await export.build_export(export.ExportSelection(PROJECT), export.ExportCaller(user_token="t"), tmp_path, max_bytes=1, on_message=_noop)


async def test_an_organisation_export_sends_the_organisation_and_no_user_token(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(fn, "download_package", _fake_function(_media_rows([]), calls))
    monkeypatch.setattr("app.services.supabase_client.create_service_client", lambda: FakeSvc([]))
    caller = export.ExportCaller(organisation_id="org-1")
    await export.build_export(export.ExportSelection(PROJECT), caller, tmp_path, max_bytes=1, on_message=_noop)
    # The organisation reaches the function through the body; download_package adds the service key.
    assert calls[0][1] is None


def test_a_caller_is_a_user_or_an_organisation():
    for kwargs in ({}, {"user_token": "t", "organisation_id": "o"}):
        with pytest.raises(ValueError):
            export.ExportCaller(**kwargs)


async def test_download_package_refuses_the_service_key_without_an_organisation(tmp_path):
    with pytest.raises(ValueError):
        await fn.download_package({"project_id": PROJECT}, tmp_path / "m.zip", user_token=None)


# ── The job, end to end with fakes ────────────────────────────────────────


@pytest.fixture
def job_env(tmp_path, monkeypatch):
    from app.jobs import store

    db = MagicMock()
    monkeypatch.setattr(store, "create_service_client", lambda: db)
    rows = _media_rows([(1, DEP_A, "jpg", True), (2, DEP_B, "jpg", False)])
    media = [{"id": r["mediaID"], "deployment_id": r["deploymentID"], "file_path": f"gdrive://{r['mediaID']}"} for r in rows]
    svc = FakeSvc(media)
    monkeypatch.setattr("app.services.supabase_client.create_service_client", lambda: svc)
    calls = []
    monkeypatch.setattr(fn, "download_package", _fake_function(rows, calls))
    monkeypatch.setattr(export, "_fetch_original", FakeDrive(fail={f"gdrive://{_mid(2)}": None}))
    monkeypatch.setattr(export, "FETCH_BACKOFF_S", 0)
    monkeypatch.setattr("app.config.settings.AZURE_STORAGE_CONNECTION_STRING", "UseDevelopmentStorage=true")
    uploads = {}

    async def upload(container, blob, local_path, content_type):
        uploads[(container, blob, content_type)] = local_path.read_bytes()

    monkeypatch.setattr("app.services.azure_storage.upload_file", upload)
    monkeypatch.setattr(
        "app.services.azure_storage.read_link", lambda container, blob, ttl, name: f"https://blob/{container}/{blob}?ttl={ttl}&name={name}"
    )
    return SimpleNamespace(svc=svc, calls=calls, uploads=uploads)


async def test_job_packages_stores_links_and_notifies(job_env, monkeypatch):
    from app.jobs import definitions, store

    job_id = await store.create_job(user_id="user-1", kind="export", label="CamtrapDP export with photos")
    await definitions.export_camtrapdp_originals_job(job_id, {"project_id": PROJECT}, {"user_token": "user-jwt"})

    job = await store.get_job(job_id)
    assert job.status.value == "completed_with_errors"
    assert job.result_url.startswith(f"https://blob/camtrapdp-exports/{job_id}.zip?ttl=86400&name=camtrapdp-{PROJECT}-")
    assert "1 of 2 photos" in job.message and "24 hours" in job.message
    assert job_env.calls[0][1] == "user-jwt"
    ((key, data),) = job_env.uploads.items()
    assert key == ("camtrapdp-exports", f"{job_id}.zip", "application/zip")
    with zipfile.ZipFile(io.BytesIO(data)) as out:
        assert f"media/{DEP_A}/{_mid(1)}.jpg" in out.namelist()
        assert _mid(2) in json.loads(out.read("datapackage.json"))["description"]
    insert = job_env.svc.other.table.return_value.insert
    row = insert.call_args.args[0]
    assert (row["user_id"], row["type"], row["data"]["link"]) == ("user-1", "system", "/processing")


async def test_job_fails_with_the_functions_message(job_env, monkeypatch):
    from app.jobs import definitions, store

    monkeypatch.setattr(fn, "download_package", _fake_function([], [], 403, "Not a member of this project."))
    job_id = await store.create_job(user_id="user-1", kind="export")
    await definitions.export_camtrapdp_originals_job(job_id, {"project_id": PROJECT}, {"user_token": "t"})
    job = await store.get_job(job_id)
    assert (job.status.value, job.error) == ("failed", "Not a member of this project.")
    assert not job_env.uploads


async def test_job_fails_when_storage_refuses(job_env, monkeypatch):
    from app.jobs import definitions, store

    monkeypatch.setattr("app.services.azure_storage.upload_file", AsyncMock(side_effect=OSError("connection reset")))
    job_id = await store.create_job(user_id="user-1", kind="export")
    await definitions.export_camtrapdp_originals_job(job_id, {"project_id": PROJECT}, {"user_token": "t"})
    job = await store.get_job(job_id)
    assert job.status.value == "failed" and "could not be saved" in job.error and job.result_url is None


async def test_job_fails_when_no_link_can_be_signed(job_env, monkeypatch):
    from app.jobs import definitions, store
    from app.services.azure_storage import AzureStorageError

    def no_key(*args):
        raise AzureStorageError("The connection string has no AccountKey to sign a link with")

    monkeypatch.setattr("app.services.azure_storage.read_link", no_key)
    job_id = await store.create_job(user_id="user-1", kind="export")
    await definitions.export_camtrapdp_originals_job(job_id, {"project_id": PROJECT}, {"user_token": "t"})
    job = await store.get_job(job_id)
    assert job.status.value == "failed" and "no download link" in job.error and job.result_url is None


async def test_job_without_storage_fails_before_reading_anything(job_env, monkeypatch):
    from app.jobs import definitions, store

    monkeypatch.setattr("app.config.settings.AZURE_STORAGE_CONNECTION_STRING", "")
    job_id = await store.create_job(user_id="user-1", kind="export")
    await definitions.export_camtrapdp_originals_job(job_id, {"project_id": PROJECT}, {"user_token": "t"})
    job = await store.get_job(job_id)
    assert job.status.value == "failed" and "no storage for exports" in job.error
    assert not job_env.calls and not job_env.uploads


def test_the_job_is_registered_for_the_worker():
    from app.jobs.definitions import JOBS

    assert "export_camtrapdp_originals_job" in {f.__name__ for f in JOBS}


# ── The route ─────────────────────────────────────────────────────────────


@pytest.fixture
def route(monkeypatch):
    from app.dependencies import get_current_user
    from app.main import app
    from app.routers import exports

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="user-1", email="u@ww.ai")
    enqueue = AsyncMock(return_value="local")
    monkeypatch.setattr(exports, "enqueue_job", enqueue)
    monkeypatch.setattr(exports, "create_job", AsyncMock(return_value="job-1"))
    monkeypatch.setattr(exports.settings, "FF_CAMTRAPDP_EXPORT_ENABLED", True)
    monkeypatch.setattr(exports.settings, "AZURE_STORAGE_CONNECTION_STRING", "UseDevelopmentStorage=true")
    yield SimpleNamespace(module=exports, enqueue=enqueue)
    app.dependency_overrides.pop(get_current_user, None)


def _post(client, body=None):
    return client.post("/api/exports/camtrapdp", json=body or {"project_id": PROJECT}, headers={"Authorization": "Bearer user-jwt"})


def test_route_starts_the_job_as_the_user(client, route, monkeypatch):
    access = AsyncMock()
    monkeypatch.setattr(route.module, "assert_access", access)
    resp = _post(client, {"project_id": PROJECT, "deployment_ids": [DEP_A]})
    assert resp.status_code == 200 and resp.json()["data"]["job_id"] == "job-1"
    access.assert_awaited_once_with("user-1", project_id=PROJECT)
    name, job_id, selection, caller = route.enqueue.await_args.args
    assert (name, job_id) == ("export_camtrapdp_originals_job", "job-1")
    assert selection["deployment_ids"] == [DEP_A] and caller == {"user_token": "user-jwt", "organisation_id": None}
    # The API's own address, for the signed download link (#462).
    assert route.enqueue.await_args.kwargs == {"link_base": "http://testserver/"}


def test_route_hides_another_organisations_project(client, route, monkeypatch):
    monkeypatch.setattr(route.module, "assert_access", AsyncMock(side_effect=HTTPException(status_code=404, detail="Not found")))
    assert _post(client).status_code == 404
    route.enqueue.assert_not_awaited()


def test_route_is_off_behind_its_flag(client, route, monkeypatch):
    monkeypatch.setattr(route.module.settings, "FF_CAMTRAPDP_EXPORT_ENABLED", False)
    assert _post(client).json()["error"]["code"] == "FEATURE_DISABLED"
    route.enqueue.assert_not_awaited()


def test_route_is_off_without_storage_for_its_zips(client, route, monkeypatch):
    # The website then falls back to the metadata-only package.
    monkeypatch.setattr(route.module.settings, "AZURE_STORAGE_CONNECTION_STRING", "")
    assert _post(client).json()["error"]["code"] == "FEATURE_DISABLED"
    route.enqueue.assert_not_awaited()


def test_route_needs_a_signed_in_user(client):
    assert client.post("/api/exports/camtrapdp", json={"project_id": PROJECT}).status_code in (401, 422)


# ── The signed download link (#462) ───────────────────────────────────────

SECRET = "test-only-dummy-export-link-secret"
JOB = "22222222-2222-4222-8222-222222222222"
NAME = f"camtrapdp-{PROJECT}-2026-11-26.zip"
API = "https://api.example/"
REFUSED = {"detail": "This download link is not valid or has expired."}


@pytest.fixture
def signed(monkeypatch):
    """The S3 backend with EXPORT_LINK_SECRET set, as on AWS once the parameter exists."""
    from app.config import settings
    from app.services import object_store

    client = object_store.new_s3_client(region_name="ap-southeast-2", aws_access_key_id="testing", aws_secret_access_key="testing")
    monkeypatch.setattr(settings, "OBJECT_STORE_BACKEND", "s3")
    monkeypatch.setattr(settings, "S3_BUCKET", "ww-test-media")
    monkeypatch.setattr(settings, "EXPORT_LINK_SECRET", SECRET)
    object_store.set_s3_client(client)
    yield settings
    object_store.set_s3_client(None)


def _query(link: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(link).query).items()}


def _local(link: str) -> str:
    """The path and query of a link, for the test client."""
    parts = urlsplit(link)
    return f"{parts.path}?{parts.query}"


def test_a_signed_link_works_until_it_expires():
    link = export.signed_link(API, JOB, NAME, 3600, SECRET, now=1000)
    assert link.startswith(f"{API}api/exports/camtrapdp/{JOB}/download?name={NAME}&expires=4600&sig=")
    q = _query(link)
    assert export.check_signed_link(JOB, q["name"], q["expires"], q["sig"], SECRET, now=4599)
    assert not export.check_signed_link(JOB, q["name"], q["expires"], q["sig"], SECRET, now=4600)


@pytest.mark.parametrize(
    "job_id, name, expires, sig, secret",
    [
        ("33333333-3333-4333-8333-333333333333", NAME, None, None, SECRET),  # another export
        (JOB, "camtrapdp-other.zip", None, None, SECRET),  # another file name
        (JOB, NAME, "9999999999", None, SECRET),  # a later expiry
        (JOB, NAME, None, "0" * 64, SECRET),  # a made-up signature
        (JOB, NAME, None, "é" * 64, SECRET),  # not even ASCII
        (JOB, NAME, None, "", SECRET),  # no signature
        (JOB, NAME, "", None, SECRET),  # no expiry
        (JOB, NAME, "4600.0", None, SECRET),  # an expiry that is not a whole number
        (JOB, NAME, None, None, "another-secret"),  # signed with another key
    ],
)
def test_an_altered_link_is_refused(job_id, name, expires, sig, secret):
    q = _query(export.signed_link(API, JOB, NAME, 3600, SECRET, now=1000))
    expires = q["expires"] if expires is None else expires
    assert not export.check_signed_link(job_id, name, expires, q["sig"] if sig is None else sig, secret, now=1000)


@pytest.mark.parametrize("job_id", ["../../originals/p/d/h", f"{JOB}/../x", "JOB", ""])
def test_a_link_can_only_name_an_export_zip(job_id):
    """Even a correctly signed link opens nothing outside exports/<uuid>.zip."""
    q = _query(export.signed_link(API, job_id, NAME, 3600, SECRET, now=1000))
    assert not export.check_signed_link(job_id, NAME, q["expires"], q["sig"], SECRET, now=1000)


@pytest.mark.parametrize("name", ['x".zip', "x.zip\r\nSet-Cookie: a=b", "x.tar", ".zip"])
def test_a_link_never_carries_an_unsafe_file_name(name):
    q = _query(export.signed_link(API, JOB, name, 3600, SECRET, now=1000))
    assert not export.check_signed_link(JOB, name, q["expires"], q["sig"], SECRET, now=1000)


def test_with_the_secret_on_s3_the_link_is_the_signed_route(signed):
    link = export.download_link(JOB, NAME, "camtrapdp-exports", API)
    assert link.startswith(f"{API}api/exports/camtrapdp/{JOB}/download?")
    q = _query(link)
    assert int(q["expires"]) - time.time() == pytest.approx(export.LINK_TTL_SECONDS, abs=5)
    assert export.check_signed_link(JOB, q["name"], q["expires"], q["sig"], SECRET)


def test_without_the_secret_the_link_is_todays_presigned_get(signed, monkeypatch):
    monkeypatch.setattr(signed, "EXPORT_LINK_SECRET", "")
    link = export.download_link(JOB, NAME, "camtrapdp-exports", API)
    assert link.startswith("https://ww-test-media.s3.ap-southeast-2.amazonaws.com/") and f"/exports/{JOB}.zip?" in link
    assert "X-Amz-Expires=86400" in link


def test_without_the_apis_address_the_link_is_todays_presigned_get(signed):
    # A job queued before this change has no link_base.
    assert f"/exports/{JOB}.zip?" in export.download_link(JOB, NAME, "camtrapdp-exports", "")


def test_azure_keeps_its_sas_link_with_the_secret_set(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "OBJECT_STORE_BACKEND", "azure")
    monkeypatch.setattr(settings, "EXPORT_LINK_SECRET", SECRET)
    monkeypatch.setattr("app.services.azure_storage.read_link", lambda container, blob, ttl, name: f"https://blob/{container}/{blob}?ttl={ttl}")
    assert export.download_link(JOB, NAME, "camtrapdp-exports", API) == f"https://blob/camtrapdp-exports/{JOB}.zip?ttl=86400"


async def test_the_job_on_s3_stores_the_signed_route(job_env, signed, monkeypatch):
    from app.jobs import definitions, store
    from app.services import object_store

    upload = AsyncMock()
    monkeypatch.setattr(object_store, "upload_export", upload)
    job_id = await store.create_job(user_id="user-1", kind="export")
    await definitions.export_camtrapdp_originals_job(job_id, {"project_id": PROJECT}, {"user_token": "t"}, link_base=API)

    job = await store.get_job(job_id)
    assert job.status.value == "completed_with_errors" and "24 hours" in job.message
    assert job.result_url.startswith(f"{API}api/exports/camtrapdp/{job_id}/download?name=camtrapdp-{PROJECT}-")
    assert upload.await_args.args[0] == f"{job_id}.zip"
    q = _query(job.result_url)
    assert export.check_signed_link(job_id, q["name"], q["expires"], q["sig"], SECRET)


def test_the_route_redirects_to_a_fresh_short_presigned_get_on_every_open(client, signed, monkeypatch):
    """A link signed 23 hours 59 minutes ago, long after the Lambda role session it began in."""
    from app.services import object_store

    real, presigned = object_store.presign_get, []

    def spy(*args, **kwargs):
        presigned.append(real(*args, **kwargs))
        return presigned[-1]

    monkeypatch.setattr(object_store, "presign_get", spy)
    link = export.signed_link(API, JOB, NAME, export.LINK_TTL_SECONDS, SECRET, now=time.time() - export.LINK_TTL_SECONDS + 60)
    for _ in range(2):
        resp = client.get(_local(link), follow_redirects=False)  # no Authorization header
        assert resp.status_code == 307 and resp.headers["cache-control"] == "no-store"
        location = resp.headers["location"]
        assert location.startswith(f"https://ww-test-media.s3.ap-southeast-2.amazonaws.com/exports/{JOB}.zip?")
        assert "X-Amz-Expires=300" in location and f'filename="{NAME}"' in unquote(location)
    assert len(presigned) == 2


def test_the_route_refuses_an_expired_link(client, signed):
    link = export.signed_link(API, JOB, NAME, export.LINK_TTL_SECONDS, SECRET, now=time.time() - export.LINK_TTL_SECONDS - 1)
    resp = client.get(_local(link), follow_redirects=False)
    assert (resp.status_code, resp.json()) == (403, REFUSED)


@pytest.mark.parametrize(
    "change",
    [
        {"name": "camtrapdp-other.zip"},
        {"expires": "9999999999"},
        {"sig": "0" * 64},
        {"expires": "soon"},
        {"name": None, "expires": None, "sig": None},
    ],
)
def test_the_route_refuses_an_altered_or_malformed_link_alike(client, signed, change):
    q = _query(export.signed_link(API, JOB, NAME, 3600, SECRET))
    q = {k: v for k, v in {**q, **change}.items() if v is not None}
    resp = client.get(f"/api/exports/camtrapdp/{JOB}/download?{urlencode(q)}", follow_redirects=False)
    assert (resp.status_code, resp.json()) == (403, REFUSED)


def test_the_route_refuses_a_link_moved_to_another_path(client, signed):
    q = _query(export.signed_link(API, JOB, NAME, 3600, SECRET))
    resp = client.get(f"/api/exports/camtrapdp/not-a-job/download?{urlencode(q)}", follow_redirects=False)
    assert (resp.status_code, resp.json()) == (403, REFUSED)


@pytest.mark.parametrize("backend, secret", [("s3", ""), ("azure", SECRET), ("local", SECRET)])
def test_the_route_is_not_there_without_signed_links(client, signed, monkeypatch, backend, secret):
    monkeypatch.setattr(signed, "OBJECT_STORE_BACKEND", backend)
    monkeypatch.setattr(signed, "EXPORT_LINK_SECRET", secret)
    link = export.signed_link(API, JOB, NAME, 3600, SECRET)
    assert client.get(_local(link), follow_redirects=False).status_code == 404
