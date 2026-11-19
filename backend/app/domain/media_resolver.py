# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Media resolver — pluggable storage backend for serving media files.

Resolves a media ``file_path`` to raw image bytes by dispatching to the
appropriate storage provider based on the path prefix.

Supported providers (extensible via ``RESOLVERS`` dict):

- ``https://`` / ``http://`` — Public URL, fetched directly
- ``gdrive://`` — Google Drive file ID, fetched via service account
- ``supabase://`` — Supabase Storage bucket/path (placeholder)
- ``s3://``: AWS S3 bucket/key, read through ``services.object_store``
- Relative paths — unresolved local files (returns None)

To add a new provider, implement an async function matching the
``Resolver`` signature and register it in the ``RESOLVERS`` dict.
"""

import asyncio
import ipaddress
import socket
from typing import Callable, Coroutine, Literal, Optional, Tuple
from urllib.parse import urlparse

import httpx
import structlog

logger = structlog.get_logger()


async def _is_safe_public_url(url: str) -> bool:
    """SSRF guard: allow only http(s) URLs whose host resolves to public IPs.

    The media proxy resolves a user-writable ``file_path``, so without this an
    authenticated user could point it at cloud metadata (169.254.169.254) or an
    internal service. We reject private/loopback/link-local/reserved/multicast
    targets up front, and the caller disables redirects so a public host can't
    bounce to an internal one. (Residual: DNS rebinding between this check and the
    request — acceptable here; pin the IP if this ever serves higher-risk paths.)
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(parsed.hostname, port, proto=socket.IPPROTO_TCP)
    except Exception as exc:
        logger.warning("media_resolve_dns_failed", host=parsed.hostname, error=str(exc))
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return True


# Type alias for image size: thumbnail (grid) or full (detail panel)
ImageSize = Literal["thumb", "full"]

# Type alias for a resolver function
# Takes the path (without prefix) and size, returns (bytes, content_type) or None
Resolver = Callable[[str, ImageSize], Coroutine[None, None, Optional[Tuple[bytes, str]]]]


# ── Provider implementations ────────────────────────────────────────


async def _resolve_public_url(url: str, size: ImageSize) -> Optional[Tuple[bytes, str]]:
    """Fetch an image from a public HTTP(S) URL (SSRF-guarded, no redirects)."""
    if not await _is_safe_public_url(url):
        logger.warning("media_resolve_url_blocked", url=url)
        return None
    try:
        # follow_redirects=False so a public host can't 30x-redirect to an internal one.
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning("media_resolve_http_error", url=url, status=resp.status_code)
                return None
            content_type = resp.headers.get("content-type", "image/jpeg")
            return resp.content, content_type
    except Exception as exc:
        logger.warning("media_resolve_http_failed", url=url, error=str(exc))
        return None


async def _resolve_gdrive(file_id: str, size: ImageSize) -> Optional[Tuple[bytes, str]]:
    """Fetch an image from Google Drive using the service account.

    - thumb: uses Drive API thumbnailLink (fast, ~200px)
    - full: downloads the complete file via get_media (high-res)
    """
    try:
        from app.services.google_drive import GoogleDriveService

        svc = GoogleDriveService()
        import asyncio

        if size == "thumb":
            # Use Drive's built-in thumbnail (much faster than full download)
            def _get_thumbnail():
                meta = (
                    svc._service.files()
                    .get(
                        fileId=file_id,
                        fields="thumbnailLink",
                        supportsAllDrives=True,
                    )
                    .execute()
                )
                return meta.get("thumbnailLink")

            thumb_url = await asyncio.to_thread(_get_thumbnail)
            if thumb_url:
                # Drive thumbnail URLs are temporary but fast
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                    resp = await client.get(thumb_url)
                    if resp.status_code == 200:
                        return resp.content, resp.headers.get("content-type", "image/jpeg")

            # Fall through to full download if thumbnail unavailable

        # Full resolution download
        def _download():
            request = svc._service.files().get_media(fileId=file_id)
            import io

            from googleapiclient.http import MediaIoBaseDownload

            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            return buffer.getvalue()

        content = await asyncio.to_thread(_download)
        return content, "image/jpeg"
    except Exception as exc:
        logger.warning("media_resolve_gdrive_failed", file_id=file_id, error=str(exc))
        return None


async def _resolve_supabase(path: str, size: ImageSize) -> Optional[Tuple[bytes, str]]:
    """Placeholder for Supabase Storage resolution."""
    logger.info("media_resolve_supabase_not_implemented", path=path)
    return None


async def _resolve_s3(path: str, size: ImageSize) -> Optional[Tuple[bytes, str]]:
    """Fetch an original archived to S3 (``s3://bucket/key``, prefix already stripped).

    There is no server-side thumbnail for an S3 original, so ``thumb`` returns the full
    image; the grid reads the Supabase renditions instead.
    """
    from app.services import object_store

    try:
        bucket, key = object_store.parse_s3_path(path)
        found = await object_store.get_object(key, bucket=bucket)
    except Exception as exc:  # noqa: BLE001 - a bad path or a refused read is "not resolvable"
        logger.warning("media_resolve_s3_failed", path=path, error=str(exc))
        return None
    if not found:
        logger.warning("media_resolve_s3_missing", path=path)
        return None
    data, content_type = found
    return data, content_type if content_type.startswith("image/") else "image/jpeg"


# ── Resolver registry ───────────────────────────────────────────────

RESOLVERS: dict[str, tuple[bool, Resolver]] = {
    "https://": (False, _resolve_public_url),
    "http://": (False, _resolve_public_url),
    "gdrive://": (True, _resolve_gdrive),
    "supabase://": (True, _resolve_supabase),
    "s3://": (True, _resolve_s3),
}


async def resolve_media(file_path: str, size: ImageSize = "full") -> Optional[Tuple[bytes, str]]:
    """Resolve a file_path to (bytes, content_type) using the appropriate provider.

    Returns None if the path cannot be resolved.
    """
    if not file_path:
        return None

    for prefix, (strip, resolver) in RESOLVERS.items():
        if file_path.startswith(prefix):
            key = file_path[len(prefix) :] if strip else file_path
            return await resolver(key, size)

    # No matching prefix — unresolvable local path
    logger.debug("media_resolve_no_provider", file_path=file_path)
    return None
