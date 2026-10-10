# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Supabase Storage adapter with retries.

Handles download/upload to Supabase Storage buckets with a
two-step fallback strategy (SDK → public URL).
"""

from datetime import datetime
from pathlib import Path
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


async def upload_file_to_storage(bucket: str, path: str, local_path: Path, content_type: str = "application/octet-stream") -> bool:
    """Upload a file from disk, streamed rather than read into memory first.

    One standard upload, so the bucket's and the project's size limits apply in full.
    Returns True on success, False on failure.
    """
    import asyncio

    client = create_service_client()

    def _upload() -> None:
        with local_path.open("rb") as fh:
            client.storage.from_(bucket).upload(path, fh, file_options={"content-type": content_type})

    try:
        await asyncio.to_thread(_upload)
        return True
    except Exception as e:
        logger.error("storage_upload_failed", bucket=bucket, path=path, error=str(e))
        return False


def signed_download_url(bucket: str, path: str, expires_in: int, filename: str) -> Optional[str]:
    """A signed URL that downloads ``path`` as ``filename``, or None when signing fails."""
    client = create_service_client()
    try:
        signed = client.storage.from_(bucket).create_signed_url(path, expires_in, {"download": filename})
    except Exception as e:
        logger.error("storage_sign_failed", bucket=bucket, path=path, error=str(e))
        return None
    return signed.get("signedURL") or signed.get("signedUrl") or None


async def delete_older_than(bucket: str, folder: str, cutoff: datetime) -> int:
    """Delete the files directly in ``folder`` created before ``cutoff``. Returns how many.

    Lists oldest first, a page at a time, and stops at the first file that is new enough.
    """
    import asyncio

    client = create_service_client()

    def _sweep() -> int:
        removed = 0
        while True:
            page = client.storage.from_(bucket).list(folder, {"limit": 100, "offset": 0, "sortBy": {"column": "created_at", "order": "asc"}})
            stale = []
            for obj in page or []:
                created = obj.get("created_at")
                if not obj.get("id") or not created:
                    continue  # a sub-folder, not a file
                if datetime.fromisoformat(created.replace("Z", "+00:00")) >= cutoff:
                    break
                stale.append(f"{folder}/{obj['name']}")
            if not stale:
                return removed
            client.storage.from_(bucket).remove(stale)
            removed += len(stale)
            if len(stale) < len(page):
                return removed

    try:
        return await asyncio.to_thread(_sweep)
    except Exception as e:
        logger.warning("storage_sweep_failed", bucket=bucket, folder=folder, error=str(e))
        return 0


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
