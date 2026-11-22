# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Transient-failure handling on the upload path.

Two failures from the 2026-09-05 person-detection bench run:

- one frame in ten lost to a single 60 s read timeout against googleapis.com,
  with no retry (GoogleDriveService.upload_file);
- a whole batch lost its Drive job because the shared Supabase client's HTTP/2
  connection had been closed (ConnectionTerminated) while the previous batch's
  job was using it (exif router, _enqueue_drive_upload).
"""

import asyncio
from unittest.mock import MagicMock

import httpx
import pytest
import requests

from app.routers import exif as exif_router
from app.services import google_drive
from app.services.google_drive import DriveTransientError, GoogleDriveService
from app.services.supabase_client import is_dropped_connection


def _service_without_credentials() -> GoogleDriveService:
    svc = GoogleDriveService.__new__(GoogleDriveService)
    svc._api_lock = asyncio.Lock()
    svc._credentials = MagicMock()
    svc._find_file_id_by_hash = lambda parent_id, file_hash: None  # not a duplicate
    return svc


def _ok(file_id: str = "drive-file-1") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"id": file_id}
    return resp


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(google_drive, "DRIVE_UPLOAD_BACKOFF_S", 0.0)


async def _upload(svc: GoogleDriveService):
    return await svc.upload_file(parent_id="folder", filename="20260905194539_01.jpg", file_bytes=b"jpeg", mime_type="image/jpeg", file_hash="abc")


class TestDriveUploadRetry:
    async def test_read_timeout_is_retried_and_the_frame_lands(self, monkeypatch):
        calls = []

        def post(*args, **kwargs):
            calls.append(1)
            if len(calls) < 3:
                raise requests.exceptions.ReadTimeout("Read timed out. (read timeout=60)")
            return _ok()

        monkeypatch.setattr(requests, "post", post)
        file_id, was_new = await _upload(_service_without_credentials())
        assert (file_id, was_new) == ("drive-file-1", True)
        assert len(calls) == 3

    async def test_gives_up_after_the_last_attempt(self, monkeypatch):
        calls = []

        def post(*args, **kwargs):
            calls.append(1)
            raise requests.exceptions.ConnectionError("connection reset")

        monkeypatch.setattr(requests, "post", post)
        with pytest.raises(DriveTransientError):
            await _upload(_service_without_credentials())
        assert len(calls) == google_drive.DRIVE_UPLOAD_ATTEMPTS

    @pytest.mark.parametrize("status", sorted(google_drive._TRANSIENT_HTTP))
    async def test_transient_http_statuses_are_retried(self, monkeypatch, status):
        calls = []

        def post(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                resp = MagicMock()
                resp.status_code = status
                resp.text = "try again"
                return resp
            return _ok("second-try")

        monkeypatch.setattr(requests, "post", post)
        file_id, _ = await _upload(_service_without_credentials())
        assert file_id == "second-try"
        assert len(calls) == 2

    async def test_a_hard_error_is_not_retried(self, monkeypatch):
        calls = []

        def post(*args, **kwargs):
            calls.append(1)
            resp = MagicMock()
            resp.status_code = 403
            resp.text = "insufficient permissions"
            return resp

        monkeypatch.setattr(requests, "post", post)
        with pytest.raises(Exception, match="HTTP 403"):
            await _upload(_service_without_credentials())
        assert len(calls) == 1


class TestEnqueueRetry:
    def test_transient_transport_errors_are_recognised(self):
        assert is_dropped_connection(httpx.RemoteProtocolError("Server disconnected"))
        assert is_dropped_connection(RuntimeError("<ConnectionTerminated error_code:1, last_stream_id:33>"))
        assert not is_dropped_connection(ValueError("bad deployment id"))

    async def test_retries_once_with_a_fresh_client(self, monkeypatch):
        attempts = []
        resets = []

        async def enqueue(**kwargs):
            attempts.append(kwargs)
            if len(attempts) == 1:
                raise RuntimeError("<ConnectionTerminated error_code:1, last_stream_id:33, additional_data:None>")
            return {"enabled": True, "status": "queued", "job_id": "job-2"}

        monkeypatch.setattr(exif_router, "_enqueue_drive_upload", enqueue)
        monkeypatch.setattr(exif_router, "reset_service_client", lambda: resets.append(1))
        out = await exif_router._enqueue_drive_upload_with_retry(files=[], run_ai=True)
        assert out["job_id"] == "job-2"
        assert len(attempts) == 2 and attempts[0] == attempts[1]
        assert resets == [1]

    async def test_a_second_transport_failure_surfaces(self, monkeypatch):
        async def enqueue(**kwargs):
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

        monkeypatch.setattr(exif_router, "_enqueue_drive_upload", enqueue)
        monkeypatch.setattr(exif_router, "reset_service_client", lambda: None)
        with pytest.raises(httpx.RemoteProtocolError):
            await exif_router._enqueue_drive_upload_with_retry(files=[])

    async def test_other_errors_are_not_retried(self, monkeypatch):
        attempts = []
        resets = []

        async def enqueue(**kwargs):
            attempts.append(1)
            raise ValueError("no such deployment")

        monkeypatch.setattr(exif_router, "_enqueue_drive_upload", enqueue)
        monkeypatch.setattr(exif_router, "reset_service_client", lambda: resets.append(1))
        with pytest.raises(ValueError):
            await exif_router._enqueue_drive_upload_with_retry(files=[])
        assert attempts == [1] and resets == []
