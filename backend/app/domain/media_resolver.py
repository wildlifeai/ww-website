# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Media resolver — pluggable storage backend for serving media files.

Resolves a media ``file_path`` to raw image bytes by dispatching to the
appropriate storage provider based on the path prefix.

Supported providers (extensible via ``RESOLVERS`` dict):

- ``https://`` / ``http://`` — Public URL, fetched directly
- ``gdrive://`` — Google Drive file ID, fetched via service account
- ``supabase://`` — Supabase Storage bucket/path (placeholder)
- ``s3://``: AWS S3 bucket/key, read through ``services.object_store``, only from the
  ``originals/<project>/<deployment>/`` folder in ``S3_BUCKET`` of the media row it belongs to
- Relative paths — unresolved local files (returns None)

To add a new provider, implement an async function matching the
``Resolver`` signature and register it in the ``RESOLVERS`` dict.

A route that hands an image to a client goes through :func:`serve_media`, which turns
what Lambda cannot answer into a link. Both take the row as ``media`` (:class:`MediaRef`).
"""

import asyncio
import hashlib
import ipaddress
import socket
import time
from dataclasses import dataclass
from typing import Any, Callable, Collection, Coroutine, Iterable, Literal, Mapping, Optional, Tuple
from urllib.parse import urlparse

import httpx
import structlog

logger = structlog.get_logger()

# Lambda answers at most 6 MB and base64-encodes a binary body, so a body above about 4.5 MB
# cannot be returned there. 4 MiB encodes to 5.6 MB, which leaves room for the headers.
INLINE_MAX_BYTES = 4 * 1024 * 1024
# A copy served by link expires with the upload buffer, a day later (infra/aws/template.yaml).
STAGED_PREFIX = "buffer/media/"


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


# ── S3 originals: only from their own folder ────────────────────────
#
# The upload job and the CamtrapDP import write each original to
# ``object_store.original_key``: ``originals/<project>/<deployment>/<file_hash>.<ext>`` in
# ``S3_BUCKET``. An ``s3://`` path is read only when it is in that folder of the media row it
# belongs to; any other is answered as a missing original.

# How long a deployment's projects are reused before they are read again.
FOLDERS_TTL_SECONDS = 60.0
_folders_cache: dict[str, tuple[float, frozenset[str]]] = {}


@dataclass(frozen=True)
class MediaRef:
    """The ``media`` row a stored path belongs to: its id and ``media.deployment_id``."""

    id: str
    deployment_id: str

    @classmethod
    def of(cls, row: Mapping[str, Any]) -> "MediaRef":
        """From a ``media`` row read with its ``id`` and ``deployment_id``."""
        return cls(str(row.get("id") or ""), str(row.get("deployment_id") or ""))


def in_own_folder(file_path: str, deployment_id: str, project_ids: Collection[str]) -> bool:
    """True when ``file_path`` is ``s3://<S3_BUCKET>/originals/<project>/<deployment_id>/<name>``
    for one of ``project_ids``."""
    from app.config import settings
    from app.services import object_store

    if not file_path.startswith("s3://") or not deployment_id or not settings.S3_BUCKET:
        return False
    try:
        bucket, key = object_store.parse_s3_path(file_path)
    except object_store.ObjectStoreError:
        return False
    if bucket != settings.S3_BUCKET or key.startswith("/") or ".." in key:
        return False
    parts = key.split("/", 3)
    if len(parts) < 4:
        return False
    top, project, deployment, name = parts
    return f"{top}/" == object_store.ORIGINALS_PREFIX and project in project_ids and deployment == deployment_id and bool(name)


def _read_projects(deployment_ids: list[str]) -> dict[str, frozenset[str]]:
    """Each deployment's project, and every project it was moved from.

    ww-backend's ``move_deployment`` leaves the originals where they were uploaded, so a moved
    deployment's photos stay in the folder of the project it had then (``deployment_moves``).
    """
    from app.services.db_utils import read_all, rows_of
    from app.services.supabase_client import create_service_client

    svc = create_service_client()
    found: dict[str, set[str]] = {d: set() for d in deployment_ids}
    for i in range(0, len(deployment_ids), 100):
        chunk = deployment_ids[i : i + 100]
        for row in rows_of(svc.table("deployments").select("id, project_id").in_("id", chunk).execute()):
            found.setdefault(str(row["id"]), set()).add(str(row.get("project_id") or ""))
        moves = read_all(
            lambda c=chunk: svc.table("deployment_moves").select("id, deployment_id, from_project_id").in_("deployment_id", c).order("id")
        )
        for row in moves:
            found.setdefault(str(row["deployment_id"]), set()).add(str(row.get("from_project_id") or ""))
    return {d: frozenset(p for p in projects if p) for d, projects in found.items()}


async def original_projects(deployment_ids: Iterable[str]) -> dict[str, frozenset[str]]:
    """For each deployment, the projects whose ``originals/<project>/<deployment>/`` folder holds its originals.

    Read with the service client and kept :data:`FOLDERS_TTL_SECONDS`, so a run over a
    deployment's photos reads them once. Raises when the database cannot be read.
    """
    now = time.monotonic()
    out: dict[str, frozenset[str]] = {}
    stale: list[str] = []
    for d in sorted({d for d in deployment_ids if d}):
        hit = _folders_cache.get(d)
        if hit and now - hit[0] < FOLDERS_TTL_SECONDS:
            out[d] = hit[1]
        else:
            stale.append(d)
    if stale:
        fresh = await asyncio.to_thread(_read_projects, stale)
        if len(_folders_cache) > 10_000:
            _folders_cache.clear()
        for d in stale:
            out[d] = fresh.get(d, frozenset())
            _folders_cache[d] = (now, out[d])
    return out


async def _s3_original_readable(file_path: str, media: Optional[MediaRef]) -> bool:
    """True when the ``s3://`` original is in its own row's folder. Otherwise the refusal is logged
    with the media id and the caller answers as for a missing original."""
    if media is None or not media.deployment_id:
        logger.warning("media_s3_original_refused", media_id=media.id if media else None, reason="no media row")
        return False
    try:
        projects = (await original_projects([media.deployment_id])).get(media.deployment_id, frozenset())
    except Exception as exc:  # noqa: BLE001 - a folder that cannot be checked is not read
        logger.warning("media_s3_original_refused", media_id=media.id, reason="folder lookup failed", error=str(exc))
        return False
    if not in_own_folder(file_path, media.deployment_id, projects):
        logger.warning("media_s3_original_refused", media_id=media.id, reason="not in its own folder")
        return False
    return True


# ── Resolver registry ───────────────────────────────────────────────

RESOLVERS: dict[str, tuple[bool, Resolver]] = {
    "https://": (False, _resolve_public_url),
    "http://": (False, _resolve_public_url),
    "gdrive://": (True, _resolve_gdrive),
    "supabase://": (True, _resolve_supabase),
    "s3://": (True, _resolve_s3),
}


def can_resolve(file_path: str) -> bool:
    """True when :func:`resolve_media` has a provider that reads ``file_path``.

    Asks ``RESOLVERS``, so a caller that skips what cannot be read (the CamtrapDP export) keeps
    up with a new provider. The ``supabase://`` placeholder reads nothing, so it does not count.
    """
    return any(file_path.startswith(prefix) for prefix, (_strip, resolver) in RESOLVERS.items() if resolver is not _resolve_supabase)


async def resolve_media(file_path: str, size: ImageSize = "full", *, media: Optional[MediaRef] = None) -> Optional[Tuple[bytes, str]]:
    """Resolve a file_path to (bytes, content_type) using the appropriate provider.

    Returns None if the path cannot be resolved. ``media`` is the row the path belongs to: an
    ``s3://`` original is read only from that row's own folder, never without it.
    """
    if not file_path:
        return None
    if file_path.startswith("s3://") and not await _s3_original_readable(file_path, media):
        return None

    for prefix, (strip, resolver) in RESOLVERS.items():
        if file_path.startswith(prefix):
            key = file_path[len(prefix) :] if strip else file_path
            return await resolver(key, size)

    # No matching prefix — unresolvable local path
    logger.debug("media_resolve_no_provider", file_path=file_path)
    return None


@dataclass(frozen=True)
class ServedMedia:
    """How a route answers for one image: its bytes, or a short-lived link to redirect to."""

    data: bytes = b""
    content_type: str = "image/jpeg"
    url: str = ""


def staged_key(file_path: str, size: ImageSize) -> str:
    """Where a copy too large to answer inline goes: one key per source and size, so a repeat overwrites it."""
    return f"{STAGED_PREFIX}{hashlib.sha256(f'{size}|{file_path}'.encode('utf-8')).hexdigest()}"


async def serve_media(file_path: str, size: ImageSize = "full", *, media: Optional[MediaRef] = None) -> Optional[ServedMedia]:
    """The image at ``file_path`` as a route should answer it, or None when it cannot be resolved.

    With the S3 object store (AWS) an ``s3://`` original is a presigned GET, and any other
    original over :data:`INLINE_MAX_BYTES` (a Drive file, a public URL) is copied under
    ``buffer/media/`` and handed out the same way: the link carries no Drive credential and
    the bytes never pass through Lambda's answer. Other backends answer the bytes, as before.
    ``media`` is the row, as for :func:`resolve_media`.
    """
    from app.config import settings
    from app.services import object_store

    on_s3 = settings.OBJECT_STORE_BACKEND == "s3"
    if on_s3 and file_path.startswith("s3://"):
        if not await _s3_original_readable(file_path, media):
            return None
        bucket, key = object_store.parse_s3_path(file_path)
        return ServedMedia(url=object_store.presign_get(key, bucket=bucket))
    resolved = await resolve_media(file_path, size=size, media=media)
    if not resolved:
        return None
    data, content_type = resolved
    if on_s3 and len(data) > INLINE_MAX_BYTES:
        key = staged_key(file_path, size)
        await object_store.put_object(key, data, content_type)
        logger.info("media_served_by_link", bytes=len(data), key=key)
        return ServedMedia(url=object_store.presign_get(key))
    return ServedMedia(data=data, content_type=content_type)
