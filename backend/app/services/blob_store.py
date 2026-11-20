# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Temporary blob storage in Redis for passing files between API and worker.

Used for model conversion: the API process stores the uploaded ZIP in Redis,
the worker retrieves it by job_id, processes it, then deletes it.

Blobs auto-expire after 1 hour as a safety net.

Under ``WORKER_BACKEND=lambda`` the job runs in the CPU function, not this process, so the
three functions hand off through the object store (``handoff-<key>`` in its buffer) instead.
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


def _shared() -> bool:
    from app.config import settings

    return settings.WORKER_BACKEND == "lambda"


async def store_blob(key: str, data: bytes, metadata: dict | None = None) -> None:
    """Store a binary blob to the local temp filesystem."""
    if _shared():
        from app.services import object_store

        await object_store.store_blob(f"handoff-{key}", data, metadata=metadata)
        return
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
    if _shared():
        from app.services import object_store

        return await object_store.retrieve_blob(f"handoff-{key}")
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
    if _shared():
        from app.services import object_store

        await object_store.delete_blob(f"handoff-{key}")
        return
    data_path = BLOB_DIR / f"{key}.data"
    meta_path = BLOB_DIR / f"{key}.meta"

    def _delete():
        if data_path.exists():
            data_path.unlink()
        if meta_path.exists():
            meta_path.unlink()

    await asyncio.to_thread(_delete)
    logger.debug("blob_deleted", key=key)
