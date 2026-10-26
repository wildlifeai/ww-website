# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Azure Blob Storage for the CamtrapDP export ZIPs (#387): the upload and the read-only link."""

import base64
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from azure.core.exceptions import ResourceExistsError

from app.services import azure_storage

KEY = base64.b64encode(b"k" * 64).decode()
CONNECTION = f"DefaultEndpointsProtocol=https;AccountName=wwtest;AccountKey={KEY};EndpointSuffix=core.windows.net"
JOB = "5f0c2a8e-1111-4111-8111-111111111111"


@pytest.fixture
def connected(monkeypatch):
    monkeypatch.setattr(azure_storage.settings, "AZURE_STORAGE_CONNECTION_STRING", CONNECTION)


def test_the_link_reads_one_blob_for_a_day_and_saves_under_its_name(connected):
    before = datetime.now(timezone.utc).replace(microsecond=0)
    url = urlparse(azure_storage.read_link("camtrapdp-exports", f"{JOB}.zip", 86400, "camtrapdp-p-2026-10-26.zip"))
    q = {k: v[0] for k, v in parse_qs(url.query).items()}

    assert (url.scheme, url.netloc, url.path) == ("https", "wwtest.blob.core.windows.net", f"/camtrapdp-exports/{JOB}.zip")
    assert (q["sp"], q["spr"], q["sr"]) == ("r", "https", "b")  # read only, HTTPS only, this blob only
    assert q["rscd"] == 'attachment; filename="camtrapdp-p-2026-10-26.zip"'
    assert q["sig"]
    expiry = datetime.fromisoformat(q["se"].replace("Z", "+00:00"))
    assert before + timedelta(hours=24) <= expiry <= before + timedelta(hours=24, seconds=5)
    assert datetime.fromisoformat(q["st"].replace("Z", "+00:00")) < before


def test_no_link_without_an_account_key(monkeypatch):
    sas_only = "BlobEndpoint=https://wwtest.blob.core.windows.net/;SharedAccessSignature=sv=2024-11-04&sig=x"
    monkeypatch.setattr(azure_storage.settings, "AZURE_STORAGE_CONNECTION_STRING", sas_only)
    with pytest.raises(azure_storage.AzureStorageError, match="AccountKey"):
        azure_storage.read_link("camtrapdp-exports", f"{JOB}.zip", 60, "x.zip")


def test_nothing_without_a_connection_string(monkeypatch):
    monkeypatch.setattr(azure_storage.settings, "AZURE_STORAGE_CONNECTION_STRING", "")
    with pytest.raises(azure_storage.AzureStorageError):
        azure_storage.read_link("camtrapdp-exports", f"{JOB}.zip", 60, "x.zip")


class FakeContainer:
    def __init__(self, exists: bool):
        self.exists = exists
        self.created = False
        self.uploads = []

    async def create_container(self):
        if self.exists:
            raise ResourceExistsError("ContainerAlreadyExists")
        self.created = self.exists = True

    async def upload_blob(self, name, data, **kwargs):
        self.uploads.append((name, data.read(), kwargs))


class FakeService:
    def __init__(self, container: FakeContainer):
        self.container = container
        self.names = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get_container_client(self, name):
        self.names.append(name)
        return self.container


@pytest.mark.parametrize("exists", [False, True])
async def test_upload_streams_the_file_into_a_private_container(monkeypatch, tmp_path, exists):
    container = FakeContainer(exists)
    service = FakeService(container)
    monkeypatch.setattr(azure_storage, "_client", lambda: service)
    zip_path = tmp_path / "export.zip"
    zip_path.write_bytes(b"PK" + b"\0" * 1000)

    await azure_storage.upload_file("camtrapdp-exports", f"{JOB}.zip", zip_path, "application/zip")

    assert service.names == ["camtrapdp-exports"]
    assert container.created is not exists  # created only when missing, never with public access
    ((name, data, kwargs),) = container.uploads
    assert (name, data) == (f"{JOB}.zip", zip_path.read_bytes())
    assert kwargs["length"] == 1002 and kwargs["overwrite"] is True
    assert kwargs["content_settings"].content_type == "application/zip"
    assert kwargs["max_concurrency"] == azure_storage.UPLOAD_CONCURRENCY


async def test_upload_needs_a_connection_string(monkeypatch, tmp_path):
    monkeypatch.setattr(azure_storage.settings, "AZURE_STORAGE_CONNECTION_STRING", "")
    zip_path = tmp_path / "export.zip"
    zip_path.write_bytes(b"PK")
    with pytest.raises(azure_storage.AzureStorageError):
        await azure_storage.upload_file("camtrapdp-exports", f"{JOB}.zip", zip_path, "application/zip")
