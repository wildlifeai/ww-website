# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Uploaded files that arrive either in the request body or as keys of objects in S3.

With ``OBJECT_STORE_BACKEND=s3`` the browser POSTs each file straight to the bucket's
``incoming/<user>/`` prefix (``routers/uploads.py`` presigns it) and the upload endpoint gets
the keys instead of the bytes, because a Lambda request body is capped near 6 MB. The
endpoints keep accepting multipart files, so the old path and local dev work unchanged.
``incoming/`` expires after a day, which also cleans up what an endpoint refused.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional, Sequence

from app.config import settings
from app.services import object_store


class IncomingError(Exception):
    """A key the caller may not use, or one with no object behind it. ``status`` is the HTTP code."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class IncomingFile:
    """The parts of ``UploadFile`` the upload endpoints use, over bytes read from S3."""

    def __init__(self, filename: str, content_type: str, data: bytes) -> None:
        self.filename = filename
        self.content_type = content_type
        self._data = data
        self.size = len(data)

    async def read(self) -> bytes:
        return self._data


def incoming_prefix(user_id: str) -> str:
    return f"{object_store.INCOMING_PREFIX}{user_id}/"


def filename_of(key: str) -> str:
    """The name the browser gave the file: ``incoming/<user>/<upload>/<n>-<name>``."""
    last = key.rsplit("/", 1)[-1]
    return last.split("-", 1)[1] if "-" in last else last


def _check_key(user_id: Optional[str], key: str) -> None:
    """Refuse a key the caller may not use: no caller, no S3, or another user's prefix."""
    if not user_id:
        raise IncomingError(401, "Sign in to send files by key.")
    if settings.OBJECT_STORE_BACKEND != "s3":
        raise IncomingError(400, "This server takes files in the request, not by key.")
    if not key.startswith(incoming_prefix(user_id)) or ".." in key:
        raise IncomingError(403, "That upload key belongs to someone else.")


async def incoming_key(user_id: Optional[str], key: str) -> str:
    """Check a key without reading it, for a job that reads the object itself; returns the file name."""
    _check_key(user_id, key)
    if not await object_store.object_exists(key):
        raise IncomingError(404, f"Nothing was uploaded at {filename_of(key)!r}; send it again.")
    return filename_of(key)


async def keep_for_job(user_id: str, data: bytes) -> str:
    """Store a file that came in the request body under the caller's prefix, for a job to read; returns its key."""
    key = f"{incoming_prefix(user_id)}{uuid.uuid4()}/0-body"
    await object_store.put_object(key, data)
    return key


async def incoming_files(user_id: Optional[str], uploads: Sequence[Any], keys: Sequence[str]) -> list[Any]:
    """``uploads`` followed by a file for each key, in order. Keys must be under the caller's prefix."""
    if not keys:
        return list(uploads)
    out = list(uploads)
    for key in keys:
        _check_key(user_id, key)
        found = await object_store.get_object(key)
        if not found:
            raise IncomingError(404, f"Nothing was uploaded at {filename_of(key)!r}; send it again.")
        data, content_type = found
        out.append(IncomingFile(filename_of(key), content_type, data))
    return out
