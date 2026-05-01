# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Temporary blob storage in Redis for passing files between API and worker.

Used for model conversion: the API process stores the uploaded ZIP in Redis,
the worker retrieves it by job_id, processes it, then deletes it.

Blobs auto-expire after 1 hour as a safety net.
"""

import asyncio
import json
import tempfile
from pathlib import Path

import structlog

logger = structlog.get_logger()

# Dedicated directory in the system temp map
BLOB_DIR = Path(tempfile.gettempdir()) / "ww_blobs"
BLOB_DIR.mkdir(parents=True, exist_ok=True)


async def store_blob(key: str, data: bytes, metadata: dict | None = None) -> None:
    """Store a binary blob to the local temp filesystem."""
    data_path = BLOB_DIR / f"{key}.data"
    meta_path = BLOB_DIR / f"{key}.meta"

    def _write():
        data_path.write_bytes(data)
        if metadata:
            with meta_path.open("w", encoding="utf-8") as f:
                json.dump(metadata, f)

    await asyncio.to_thread(_write)
    logger.debug("blob_stored", key=key, size_bytes=len(data))


async def retrieve_blob(key: str) -> tuple[bytes | None, dict | None]:
    """Retrieve a blob and its metadata from the local temp filesystem."""
    data_path = BLOB_DIR / f"{key}.data"
    meta_path = BLOB_DIR / f"{key}.meta"

    def _read():
        data = None
        metadata = None
        if data_path.exists():
            data = data_path.read_bytes()
        if meta_path.exists():
            with meta_path.open("r", encoding="utf-8") as f:
                metadata = json.load(f)
        return data, metadata

    return await asyncio.to_thread(_read)


async def delete_blob(key: str) -> None:
    """Delete a blob and its metadata from disk."""
    data_path = BLOB_DIR / f"{key}.data"
    meta_path = BLOB_DIR / f"{key}.meta"

    def _delete():
        if data_path.exists():
            data_path.unlink()
        if meta_path.exists():
            meta_path.unlink()

    await asyncio.to_thread(_delete)
    logger.debug("blob_deleted", key=key)
