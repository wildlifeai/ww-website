# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Input limits on uploaded ZIPs (domain/zip_limits.py): a CamtrapDP package and a model upload are
refused on their declared sizes, before any member is read or written."""

from __future__ import annotations

import io
import json
import tempfile
import uuid
import zipfile
from pathlib import Path

import pytest

from app.domain import zip_limits
from app.domain.camtrapdp import parse_zip
from app.domain.model import ModelDomainError, convert_uploaded_model

MB = zip_limits.MB


def _zip(members: dict[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _package(**extra: bytes) -> dict[str, bytes]:
    return {
        "datapackage.json": json.dumps({"title": "t"}).encode(),
        "deployments.csv": b"deploymentID,deploymentStart\nd1,2026-01-01T00:00:00Z\n",
        **extra,
    }


@pytest.fixture(scope="module")
def gigabyte_of_zeros() -> bytes:
    """One member declaring 1 GiB of zeros, about 1 MB compressed."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in _package().items():
            zf.writestr(name, data)
        with zf.open("media.csv", "w", force_zip64=True) as f:
            chunk = bytes(MB)
            for _ in range(1024):
                f.write(chunk)
    out = buf.getvalue()
    assert len(out) < 2 * MB
    return out


@pytest.fixture
def nothing_is_read(monkeypatch):
    """Fail the test if a member is read or extracted."""

    def refuse(*_a, **_kw):
        raise AssertionError("a member was read before the limits were checked")

    monkeypatch.setattr(zipfile.ZipFile, "read", refuse)
    monkeypatch.setattr(zipfile.ZipFile, "extractall", refuse)


# ── CamtrapDP packages ───────────────────────────────────────────────


def test_a_package_declaring_a_gigabyte_is_refused_before_anything_is_read(gigabyte_of_zeros, nothing_is_read):
    with pytest.raises(zip_limits.ZipLimitError, match="unpacks to more than 300 MB"):
        parse_zip(gigabyte_of_zeros)


def test_a_package_of_more_than_20000_files_is_refused(nothing_is_read):
    members = _package(**{f"media/{n}.txt": b"" for n in range(zip_limits.CAMTRAPDP_MAX_MEMBERS - 1)})  # 20,001 in all
    with pytest.raises(zip_limits.ZipLimitError, match="more than 20,000 files"):
        parse_zip(_zip(members))


def test_a_member_compressed_more_than_100_to_1_is_refused(nothing_is_read):
    with pytest.raises(zip_limits.ZipLimitError, match="'observations.csv' in the package is compressed more than 100 to 1"):
        parse_zip(_zip(_package(**{"observations.csv": bytes(2 * MB)})))


def test_a_datapackage_json_over_1_mb_is_refused(nothing_is_read):
    big = {"datapackage.json": b" " * (MB + 1)}
    with pytest.raises(zip_limits.ZipLimitError, match="datapackage.json is over the 1 MB limit"):
        parse_zip(_zip({**_package(), **big}, zipfile.ZIP_STORED))


def test_a_csv_or_a_photo_over_its_limit_is_refused(monkeypatch, nothing_is_read):
    monkeypatch.setattr(zip_limits, "CAMTRAPDP_MAX_CSV_BYTES", 10)
    with pytest.raises(zip_limits.ZipLimitError, match="deployments.csv is over the"):
        parse_zip(_zip(_package(), zipfile.ZIP_STORED))
    monkeypatch.setattr(zip_limits, "CAMTRAPDP_MAX_CSV_BYTES", MB)
    monkeypatch.setattr(zip_limits, "CAMTRAPDP_MAX_MEDIA_BYTES", 10)
    with pytest.raises(zip_limits.ZipLimitError, match="'media/a.jpg' is over the"):
        parse_zip(_zip(_package(**{"media/a.jpg": b"x" * 11}), zipfile.ZIP_STORED))


def test_rows_are_counted_across_the_csvs_while_they_are_read(monkeypatch):
    monkeypatch.setattr(zip_limits, "CAMTRAPDP_MAX_ROWS", 3)
    media = b"mediaID,deploymentID\n" + b"".join(b"m%d,d1\n" % n for n in range(2))
    assert len(parse_zip(_zip(_package(**{"media.csv": media}))).media) == 2  # 1 + 2 rows: at the limit

    media += b"m2,d1\n"
    with pytest.raises(zip_limits.ZipLimitError, match="more than 3 rows"):
        parse_zip(_zip(_package(**{"media.csv": media})))


def test_a_package_within_the_limits_reads_as_before():
    pkg = parse_zip(_zip(_package(**{"media/IMG1.JPG": b"\xff\xd8 photo", "media.csv": b"mediaID,deploymentID,filePath\nm1,d1,media/IMG1.JPG\n"})))
    assert pkg.metadata == {"title": "t"} and len(pkg.deployments) == 1 and len(pkg.media) == 1
    assert pkg.zip_files["media/IMG1.JPG"] == pkg.zip_files["IMG1.JPG"] == b"\xff\xd8 photo"


# ── model uploads ────────────────────────────────────────────────────


async def test_a_model_zip_declaring_a_gigabyte_is_refused_before_it_is_extracted(gigabyte_of_zeros, nothing_is_read):
    with pytest.raises(ModelDomainError, match="model ZIP unpacks to more than 1,024 MB"):
        await convert_uploaded_model(gigabyte_of_zeros, "model.zip")


async def test_a_model_zip_of_more_than_20000_files_is_refused(nothing_is_read):
    zip_bytes = _zip({f"sdk/{n}.h": b"" for n in range(zip_limits.MODEL_MAX_MEMBERS + 1)})
    with pytest.raises(ModelDomainError, match="more than 20,000 files"):
        await convert_uploaded_model(zip_bytes, "model.zip")


async def test_a_model_zip_member_compressed_more_than_100_to_1_is_refused(nothing_is_read):
    with pytest.raises(ModelDomainError, match="'trained.tflite' in the model ZIP is compressed more than 100 to 1"):
        await convert_uploaded_model(_zip({"trained.tflite": bytes(2 * MB)}), "model.zip")


async def test_the_upload_name_and_member_names_cannot_leave_the_temporary_directory():
    """The uploaded file name is not a path; extractall drops '..' from member names."""
    escape = f"escape-{uuid.uuid4().hex}.zip"
    zip_bytes = _zip({"../out/MOD00001.tfl": b"\x00tfl", "../../labels.txt": b"rat\n"})

    tfl, _txt, labels = await convert_uploaded_model(zip_bytes, f"../{escape}")

    assert tfl == b"\x00tfl" and labels == ["rat"]
    assert not (Path(tempfile.gettempdir()) / escape).exists()
