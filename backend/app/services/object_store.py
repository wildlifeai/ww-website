# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""One object store over Azure Blob, Amazon S3 and a local directory.

``OBJECT_STORE_BACKEND`` picks the backend for the upload buffer, the CamtrapDP export
ZIPs and the batch results parts:

- ``azure`` (the default, today): delegates to :mod:`app.services.azure_storage`, looked up
  at call time so the Azure code path is unchanged.
- ``s3``: ``S3_BUCKET`` through boto3, each call in a thread. Keys follow the layout in the
  migration report: ``buffer/``, ``exports/``, ``incoming/``, ``originals/``, ``results/``.
- ``local``: files under ``LOCAL_OBJECT_STORE_DIR``, for dev and tests.

``s3://bucket/key`` paths (originals archived with ``ARCHIVE_BACKEND=s3``) are read through
:func:`get_object` and handed out through :func:`presign_get`, whatever the backend, so a
rollback to Azure can still serve them. :class:`S3Archive` stands in for
``GoogleDriveService.upload_analysis_images`` when ``ARCHIVE_BACKEND=s3``.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import structlog

from app.config import settings

logger = structlog.get_logger()

BUFFER_PREFIX = "buffer/"
EXPORTS_PREFIX = "exports/"
INCOMING_PREFIX = "incoming/"
ORIGINALS_PREFIX = "originals/"
RESULTS_PREFIX = "results/"

# Parallel archive writes per upload batch, as for Drive.
MAX_CONCURRENT_PUTS = 5


class ObjectStoreError(Exception):
    """The object store is not set up for what was asked, or a key is unsafe."""


# ── s3:// paths ──────────────────────────────────────────────────────


def s3_uri(key: str, bucket: Optional[str] = None) -> str:
    """``s3://<bucket>/<key>`` for a key in ``S3_BUCKET`` (or ``bucket``)."""
    b = bucket or settings.S3_BUCKET
    if not b:
        raise ObjectStoreError("S3_BUCKET is not set")
    return f"s3://{b}/{key}"


def parse_s3_path(path: str) -> Tuple[str, str]:
    """``(bucket, key)`` from ``s3://bucket/key`` or from ``bucket/key`` (the resolver strips the scheme)."""
    rest = path[len("s3://") :] if path.startswith("s3://") else path
    bucket, _, key = rest.partition("/")
    if not bucket or not key:
        raise ObjectStoreError(f"Not an s3 path: {path!r}")
    return bucket, key


# ── S3 client ────────────────────────────────────────────────────────

_s3_client: Any = None


def new_s3_client(**kwargs: Any) -> Any:
    """A boto3 S3 client as the app uses it: SigV4 (presigned URLs need it) and standard retries.

    With a region, the client uses that region's endpoint. Without one, boto3 presigns against
    the global ``<bucket>.s3.amazonaws.com``, which does not serve a bucket in an opt-in region
    such as ``ap-southeast-6``: the browser's presigned POST then fails with no CORS answer.
    """
    import boto3
    from botocore.config import Config

    kwargs.setdefault("region_name", settings.S3_REGION or None)
    if kwargs["region_name"] and "endpoint_url" not in kwargs:
        kwargs["endpoint_url"] = f"https://s3.{kwargs['region_name']}.amazonaws.com"
    config = Config(signature_version="s3v4", s3={"addressing_style": "virtual"}, retries={"max_attempts": 4, "mode": "standard"})
    return boto3.client("s3", config=config, **kwargs)


def s3_client() -> Any:
    """The process-wide S3 client."""
    global _s3_client
    if _s3_client is None:
        _s3_client = new_s3_client()
    return _s3_client


def set_s3_client(client: Any) -> None:
    """Replace the S3 client (tests wrap one in ``botocore.stub.Stubber``)."""
    global _s3_client
    _s3_client = client


def _bucket() -> str:
    if not settings.S3_BUCKET:
        raise ObjectStoreError("S3_BUCKET is not set")
    return settings.S3_BUCKET


def _is_missing(exc: Exception) -> bool:
    code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
    return code in ("NoSuchKey", "404", "NotFound")


# ── local backend ────────────────────────────────────────────────────


def _local_root() -> Path:
    root = Path(settings.LOCAL_OBJECT_STORE_DIR) if settings.LOCAL_OBJECT_STORE_DIR else Path(tempfile.gettempdir()) / "ww_objects"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _local_path(key: str) -> Path:
    """The file for ``key``, refusing anything that would land outside the root."""
    root = _local_root().resolve()
    target = (root / key).resolve()
    if root not in target.parents:
        raise ObjectStoreError(f"Unsafe object key: {key!r}")
    return target


# ── Generic objects (results parts and their markers, incoming, archive) ──
# On the azure backend these use the upload buffer container, so the batch path can be
# tried with the API still on Azure.


async def put_object(key: str, data: bytes, content_type: str = "application/octet-stream", metadata: Optional[Dict[str, str]] = None) -> None:
    """Write ``data`` at ``key``, replacing what is there."""
    if settings.OBJECT_STORE_BACKEND == "azure":
        from app.services import azure_storage

        await azure_storage.store_blob(key, data, metadata=metadata)
        return
    if settings.OBJECT_STORE_BACKEND == "local":

        def _write() -> None:
            path = _local_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            if metadata:
                path.with_name(path.name + ".meta").write_text(json.dumps(metadata), encoding="utf-8")

        await asyncio.to_thread(_write)
        return
    bucket = _bucket()
    await asyncio.to_thread(s3_client().put_object, Bucket=bucket, Key=key, Body=data, ContentType=content_type, Metadata=metadata or {})


async def get_object(key: str, bucket: Optional[str] = None) -> Optional[Tuple[bytes, str]]:
    """``(bytes, content_type)`` at ``key``, or None when it does not exist.

    With ``bucket`` given (an ``s3://`` path) S3 is read whatever the backend, so a rollback
    to Azure still serves originals archived to S3.
    """
    if bucket is None and settings.OBJECT_STORE_BACKEND == "azure":
        from app.services import azure_storage

        data, _meta = await azure_storage.retrieve_blob(key)
        return (data, "application/octet-stream") if data is not None else None
    if bucket is None and settings.OBJECT_STORE_BACKEND == "local":

        def _read() -> Optional[Tuple[bytes, str]]:
            path = _local_path(key)
            return (path.read_bytes(), "application/octet-stream") if path.is_file() else None

        return await asyncio.to_thread(_read)

    b = bucket or _bucket()

    def _get() -> Optional[Tuple[bytes, str]]:
        try:
            resp = s3_client().get_object(Bucket=b, Key=key)
        except Exception as exc:
            if _is_missing(exc):
                return None
            raise
        return resp["Body"].read(), resp.get("ContentType") or "application/octet-stream"

    return await asyncio.to_thread(_get)


async def object_exists(key: str) -> bool:
    """True when ``key`` exists."""
    if settings.OBJECT_STORE_BACKEND == "azure":
        return await get_object(key) is not None
    if settings.OBJECT_STORE_BACKEND == "local":
        return await asyncio.to_thread(lambda: _local_path(key).is_file())
    bucket = _bucket()

    def _head() -> bool:
        try:
            s3_client().head_object(Bucket=bucket, Key=key)
            return True
        except Exception as exc:
            if _is_missing(exc):
                return False
            raise

    return await asyncio.to_thread(_head)


async def delete_object(key: str) -> None:
    """Delete ``key``; a missing key is not an error."""
    if settings.OBJECT_STORE_BACKEND == "azure":
        from app.services import azure_storage

        await azure_storage.delete_blob(key)
        return
    if settings.OBJECT_STORE_BACKEND == "local":
        await asyncio.to_thread(lambda: _local_path(key).unlink(missing_ok=True))
        return
    bucket = _bucket()
    await asyncio.to_thread(s3_client().delete_object, Bucket=bucket, Key=key)


async def copy_object(src_key: str, dst_key: str) -> None:
    """Server-side copy within the bucket (s3) or the root (local)."""
    if settings.OBJECT_STORE_BACKEND == "local":

        def _copy() -> None:
            dst = _local_path(dst_key)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(_local_path(src_key).read_bytes())

        await asyncio.to_thread(_copy)
        return
    bucket = _bucket()
    await asyncio.to_thread(s3_client().copy_object, Bucket=bucket, Key=dst_key, CopySource={"Bucket": bucket, "Key": src_key})


def presign_get(key: str, ttl_seconds: Optional[int] = None, *, bucket: Optional[str] = None, filename: Optional[str] = None) -> str:
    """A GET URL for one S3 object that works for ``ttl_seconds`` without signing in.

    A URL signed with temporary credentials (a Lambda role) also dies when those expire, so
    hand these out for minutes, never for the day an emailed link must last.
    """
    params: Dict[str, Any] = {"Bucket": bucket or _bucket(), "Key": key}
    if filename:
        params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
    return s3_client().generate_presigned_url("get_object", Params=params, ExpiresIn=ttl_seconds or settings.S3_PRESIGN_GET_SECONDS)


def presign_put(key: str, ttl_seconds: int, content_type: str = "application/octet-stream") -> str:
    """A PUT URL for one key in ``S3_BUCKET``; the uploader must send the same Content-Type."""
    return s3_client().generate_presigned_url(
        "put_object", Params={"Bucket": _bucket(), "Key": key, "ContentType": content_type}, ExpiresIn=ttl_seconds
    )


def presign_post(key: str, max_bytes: int, ttl_seconds: int, content_type: Optional[str] = None) -> Dict[str, Any]:
    """A browser form POST straight to S3 for one key, refused by S3 above ``max_bytes``."""
    conditions: List[Any] = [["content-length-range", 1, max_bytes]]
    fields: Dict[str, str] = {}
    if content_type:
        fields["Content-Type"] = content_type
        conditions.append({"Content-Type": content_type})
    return s3_client().generate_presigned_post(Bucket=_bucket(), Key=key, Fields=fields or None, Conditions=conditions, ExpiresIn=ttl_seconds)


# ── Upload buffer (the three functions exif.py and the upload job use) ──


async def store_blob(key: str, data: bytes, metadata: dict | None = None) -> None:
    """Buffer an uploaded file until the upload job archives it."""
    if settings.OBJECT_STORE_BACKEND == "azure":
        from app.services import azure_storage

        await azure_storage.store_blob(key, data, metadata=metadata)
        return
    await put_object(BUFFER_PREFIX + key, data, metadata={k: str(v) for k, v in (metadata or {}).items()} or None)
    logger.debug("object_buffer_stored", key=key, size_bytes=len(data), backend=settings.OBJECT_STORE_BACKEND)


async def retrieve_blob(key: str) -> tuple[bytes | None, dict | None]:
    """A buffered file and its metadata, ``(None, None)`` when it is gone."""
    if settings.OBJECT_STORE_BACKEND == "azure":
        from app.services import azure_storage

        return await azure_storage.retrieve_blob(key)
    try:
        found = await get_object(BUFFER_PREFIX + key)
        meta = await _metadata(BUFFER_PREFIX + key) if found else {}
    except Exception as exc:  # noqa: BLE001 - as the Azure path: a missing buffer is (None, None)
        logger.warning("object_buffer_read_failed", key=key, error=str(exc))
        return None, None
    return (found[0], meta) if found else (None, None)


async def _metadata(key: str) -> dict:
    """The metadata stored with ``key`` (s3 and local)."""
    if settings.OBJECT_STORE_BACKEND == "local":

        def _read() -> dict:
            meta = _local_path(key).with_name(_local_path(key).name + ".meta")
            return json.loads(meta.read_text(encoding="utf-8")) if meta.is_file() else {}

        return await asyncio.to_thread(_read)
    bucket = _bucket()
    resp = await asyncio.to_thread(s3_client().head_object, Bucket=bucket, Key=key)
    return dict(resp.get("Metadata") or {})


async def delete_blob(key: str) -> None:
    """Drop a buffered file; best-effort, as the Azure path."""
    if settings.OBJECT_STORE_BACKEND == "azure":
        from app.services import azure_storage

        await azure_storage.delete_blob(key)
        return
    try:
        await delete_object(BUFFER_PREFIX + key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("object_buffer_delete_failed", key=key, error=str(exc))


# ── CamtrapDP export ZIPs ─────────────────────────────────────────────


async def upload_export(name: str, local_path: Path, content_type: str = "application/zip", *, container: Optional[str] = None) -> None:
    """Store an export ZIP from disk; large files go up in parts, never whole in memory."""
    if settings.OBJECT_STORE_BACKEND == "azure":
        from app.services import azure_storage

        await azure_storage.upload_file(container or settings.CAMTRAPDP_EXPORT_CONTAINER, name, local_path, content_type)
        return
    key = EXPORTS_PREFIX + name
    if settings.OBJECT_STORE_BACKEND == "local":

        def _copy() -> None:
            dst = _local_path(key)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(local_path.read_bytes())

        await asyncio.to_thread(_copy)
        return
    bucket = _bucket()
    # upload_file switches to a multipart upload above its threshold (8 MB by default).
    await asyncio.to_thread(s3_client().upload_file, str(local_path), bucket, key, ExtraArgs={"ContentType": content_type})
    logger.info("s3_export_stored", key=key, size_bytes=local_path.stat().st_size)


def export_link(name: str, ttl_seconds: int, filename: str, *, container: Optional[str] = None) -> str:
    """A read-only link to one export ZIP that saves as ``filename``."""
    if settings.OBJECT_STORE_BACKEND == "azure":
        from app.services import azure_storage

        return azure_storage.read_link(container or settings.CAMTRAPDP_EXPORT_CONTAINER, name, ttl_seconds, filename)
    if settings.OBJECT_STORE_BACKEND == "local":
        return _local_path(EXPORTS_PREFIX + name).as_uri()
    return presign_get(EXPORTS_PREFIX + name, ttl_seconds, filename=filename)


# ── Originals archive (ARCHIVE_BACKEND=s3) ────────────────────────────


def original_key(project_id: str, deployment_id: str, file_hash: str, ext: str = "jpg") -> str:
    """Where an original lives: keyed by ``media.file_hash``, so a repeat upload lands on the same key
    and camera filenames that repeat across SD cards never collide."""
    return f"{ORIGINALS_PREFIX}{project_id}/{deployment_id}/{file_hash}.{ext}"


def archived_path(uploaded: Dict[str, Any]) -> str:
    """``media.file_path`` for one archived file: its ``s3://`` path, or ``gdrive://<id>`` for Drive."""
    return uploaded.get("file_path") or f"gdrive://{uploaded['file_id']}"


class S3Archive:
    """``GoogleDriveService.upload_analysis_images`` over ``S3_BUCKET``, same arguments and stats.

    Each file goes to :func:`original_key`. A key that already exists counts as ``skipped``
    with ``was_new`` False, as a Drive duplicate does, so the upload job still registers a
    media row that is missing.
    """

    async def upload_analysis_images(self, files: List[Dict[str, Any]], file_callback: Optional[Callable] = None) -> Dict[str, Any]:
        from app.services.google_drive import compute_file_hash, sanitize_filename

        if not settings.S3_BUCKET:
            raise RuntimeError("ARCHIVE_BACKEND=s3 but S3_BUCKET is not set.")
        sem = asyncio.Semaphore(MAX_CONCURRENT_PUTS)
        stats = {"uploaded": 0, "skipped": 0, "failed": 0}
        uploaded_files: List[Dict[str, Any]] = []
        total = len(files)
        done = 0

        async def _one(info: Dict[str, Any]) -> None:
            nonlocal done
            async with sem:
                name = info.get("filename", "")
                try:
                    project, deployment = info.get("project"), info.get("deployment")
                    if not project or not deployment:
                        raise ValueError("no matching deployment for this photo")
                    data = info["file_bytes"]
                    file_hash = compute_file_hash(data, deployment["id"])
                    key = original_key(project["id"], deployment["id"], file_hash)
                    was_new = not await _exists_s3(key)
                    if was_new:
                        await asyncio.to_thread(s3_client().put_object, Bucket=_bucket(), Key=key, Body=data, ContentType="image/jpeg")
                    done += 1
                    stats["uploaded" if was_new else "skipped"] += 1
                    archive_name = info.get("drive_filename") or sanitize_filename(info.get("timestamp"), name)
                    uploaded_files.append(
                        {
                            "file_id": key,
                            "file_path": s3_uri(key),
                            "media_id": info.get("media_id"),
                            "deployment_id": deployment["id"],
                            "filename": archive_name,
                            "timestamp": info.get("timestamp"),
                            "file_hash": file_hash,
                            "was_new": was_new,
                            "exif": info.get("exif"),
                        }
                    )
                    if file_callback:
                        await file_callback(action="uploaded" if was_new else "skipped", filename=archive_name, index=done, total=total)
                except Exception as exc:  # noqa: BLE001 - one bad file never sinks the batch
                    done += 1
                    stats["failed"] += 1
                    logger.error("s3_archive_upload_error", filename=name, error=str(exc))
                    if file_callback:
                        await file_callback(action="failed", filename=name, index=done, total=total, error=str(exc))

        await asyncio.gather(*[_one(f) for f in files])
        logger.info("s3_archive_batch_complete", **stats)
        return {**stats, "files": uploaded_files}


async def _exists_s3(key: str) -> bool:
    bucket = _bucket()

    def _head() -> bool:
        try:
            s3_client().head_object(Bucket=bucket, Key=key)
            return True
        except Exception as exc:
            if _is_missing(exc):
                return False
            raise

    return await asyncio.to_thread(_head)


def archive_service() -> Any:
    """The archive the upload job writes originals to, by ``ARCHIVE_BACKEND``."""
    if settings.ARCHIVE_BACKEND == "s3":
        return S3Archive()
    from app.services.google_drive import GoogleDriveService

    return GoogleDriveService()
