# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Input limits for uploaded ZIPs, checked on the central directory before any member is read.

Two callers: ``domain/camtrapdp.py::parse_zip`` (a CamtrapDP package, read into memory) and
``domain/model.py::convert_uploaded_model`` (a model upload, extracted to disk). Python's
``zipfile`` stops reading a member at its declared ``file_size`` and checks its CRC, so the
declared sizes bound what reading the members can produce.

The numbers are first guesses, to tune with a real large package and a real Edge Impulse export.
"""

from __future__ import annotations

import zipfile
from typing import Sequence

MB = 1024 * 1024

# Any member over RATIO_FROM_BYTES may be at most MAX_RATIO times its compressed size. CSV text
# compresses about 5 to 20 times, photos and models about once.
RATIO_FROM_BYTES = 1 * MB
MAX_RATIO = 100

# CamtrapDP packages: at most 50 MB compressed (camtrapdp.MAX_UPLOAD_BYTES). Photos barely
# compress, so a real package unpacks to about 50 to 150 MB; CSV rows are held as dicts, several
# times their text size in memory, and inserted 100 at a time within the cpu function's 15 minutes.
CAMTRAPDP_MAX_MEMBERS = 20_000
CAMTRAPDP_MAX_TOTAL_BYTES = 300 * MB
CAMTRAPDP_MAX_DATAPACKAGE_BYTES = 1 * MB
CAMTRAPDP_MAX_CSV_BYTES = 100 * MB
CAMTRAPDP_MAX_MEDIA_BYTES = 50 * MB  # one photo or video, as the photo upload cap
CAMTRAPDP_MAX_ROWS = 200_000  # across deployments.csv, media.csv and observations.csv

# Model uploads: at most 50 MB compressed, extracted to the job's disk. An Edge Impulse export
# carries its SDK sources, thousands of small files.
MODEL_MAX_MEMBERS = 20_000
MODEL_MAX_TOTAL_BYTES = 1024 * MB


class ZipLimitError(ValueError):
    """A ZIP over one of the limits. The message names the limit."""


def megabytes(n: int) -> str:
    return f"{n // MB:,} MB"


def check_members(infos: Sequence[zipfile.ZipInfo], *, what: str, max_members: int, max_total_bytes: int) -> None:
    """Refuse a ZIP with too many members, too large a declared total, or a member compressed too far."""
    if len(infos) > max_members:
        raise ZipLimitError(f"The {what} holds more than {max_members:,} files, the limit.")
    if sum(i.file_size for i in infos) > max_total_bytes:
        raise ZipLimitError(f"The {what} unpacks to more than {megabytes(max_total_bytes)}, the limit.")
    for i in infos:
        if i.file_size > RATIO_FROM_BYTES and i.file_size > MAX_RATIO * max(i.compress_size, 1):
            raise ZipLimitError(
                f"'{i.filename}' in the {what} is compressed more than {MAX_RATIO} to 1, the limit for a file over {megabytes(RATIO_FROM_BYTES)}."
            )


def check_size(info: zipfile.ZipInfo, limit: int, what: str) -> None:
    """Refuse one member over ``limit`` bytes."""
    if info.file_size > limit:
        raise ZipLimitError(f"{what} is over the {megabytes(limit)} limit.")
