# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Supabase Storage adapter with retries.

Handles download/upload to Supabase Storage buckets with a
two-step fallback strategy (SDK → public URL).
"""

from typing import Optional

import structlog

from app.config import settings
from app.services.http_client import DownloadError, download_url_content
from app.services.supabase_client import create_service_client

logger = structlog.get_logger()


async def download_from_storage(bucket: str, path: str, *, silent: bool = False) -> Optional[bytes]:
    """Download a file from Supabase Storage.

    Tries SDK first, falls back to public URL.

    Returns:
        File content as bytes, or None on failure.
    """
    # Defence-in-depth: a NULL/empty path (e.g. an ai_model still converting, whose
    # model_path/labels_path are NULL) is "nothing to download", not an error.
    if not path:
        return None

    client = create_service_client()

    # Step 1: SDK download
    try:
        response = client.storage.from_(bucket).download(path)
        if response:
            return response
    except Exception as sdk_error:
        if not silent:
            logger.warning("sdk_download_failed", bucket=bucket, path=path, error=str(sdk_error))

    # Step 2: Public URL fallback
    try:
        base_url = settings.SUPABASE_URL
        if not base_url.endswith("/"):
            base_url += "/"
        public_url = f"{base_url}storage/v1/object/public/{bucket}/{path}"
        return await download_url_content(public_url)
    except DownloadError as fallback_error:
        if not silent:
            logger.error(
                "storage_download_failed",
                bucket=bucket,
                path=path,
                error=str(fallback_error),
            )
        return None


async def upload_to_storage(bucket: str, path: str, content: bytes, content_type: str = "application/octet-stream") -> bool:
    """Upload a file to Supabase Storage.

    Returns True on success, False on failure.
    """
    import asyncio

    client = create_service_client()
    try:
        await asyncio.to_thread(
            client.storage.from_(bucket).upload,
            path,
            content,
            file_options={"content-type": content_type},
        )
        return True
    except Exception as e:
        logger.error("storage_upload_failed", bucket=bucket, path=path, error=str(e))
        return False


async def upload_rendition(path: str, content: bytes, content_type: str = "image/jpeg", bucket: Optional[str] = None) -> Optional[str]:
    """Upload a public CDN rendition (thumbnail/preview/animal crop).

    Writes to the public media bucket and returns the stable public URL served by
    Supabase's CDN, or None on failure. Upserts so re-runs (backfill / reprocess)
    overwrite the existing object. Originals stay in Google Drive — only these
    small derivatives live here.
    """
    import asyncio

    bucket = bucket or settings.SUPABASE_MEDIA_BUCKET
    client = create_service_client()
    try:
        await asyncio.to_thread(
            client.storage.from_(bucket).upload,
            path,
            content,
            file_options={"content-type": content_type, "upsert": "true"},
        )
    except Exception as e:
        logger.error("rendition_upload_failed", bucket=bucket, path=path, error=str(e))
        return None

    base_url = settings.SUPABASE_URL.rstrip("/")
    return f"{base_url}/storage/v1/object/public/{bucket}/{path}"


async def delete_from_storage(bucket: str, paths: list[str]) -> bool:
    """Delete a list of files from Supabase Storage.

    Returns True on success, False on failure.
    """
    client = create_service_client()
    try:
        if paths:
            client.storage.from_(bucket).remove(paths)
        return True
    except Exception as e:
        logger.error("storage_delete_failed", bucket=bucket, path_count=len(paths), error=str(e))
        return False


async def delete_from_storage_with_progress(
    bucket: str,
    paths: list[str],
    progress_callback=None,
    batch_size: int = 10,
) -> bool:
    """Delete files from Supabase Storage in batches with progress callbacks.

    Parameters
    ----------
    bucket : str
        Storage bucket name.
    paths : list[str]
        File paths to delete.
    progress_callback : callable, optional
        Awaitable called with ``(completed, total)`` after each batch.
    batch_size : int
        Number of files to delete per API call (default 10).

    Returns True on full success, False on failure.
    """
    client = create_service_client()
    total = len(paths)
    completed = 0

    try:
        for i in range(0, total, batch_size):
            batch = paths[i : i + batch_size]
            if batch:
                client.storage.from_(bucket).remove(batch)
            completed += len(batch)
            if progress_callback:
                await progress_callback(completed, total)
        return True
    except Exception as e:
        logger.error(
            "storage_batch_delete_failed",
            bucket=bucket,
            path_count=total,
            completed=completed,
            error=str(e),
        )
        return False
