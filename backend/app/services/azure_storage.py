# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Azure Blob Storage: the temporary upload buffer, and the CamtrapDP export ZIPs.

The buffer replaces the local temp folder blob store to allow distributed buffering across
Container Apps. The exports go to their own private container (``upload_file``) and are
handed out as read-only links (``read_link``).
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import structlog
from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobSasPermissions, ContentSettings, generate_blob_sas
from azure.storage.blob.aio import BlobServiceClient

from app.config import settings

logger = structlog.get_logger()

# Parallel block uploads per file. The SDK sends a file above 64 MiB as 4 MiB blocks.
UPLOAD_CONCURRENCY = 4
# A link's start is backdated this much, so a clock behind Azure's does not refuse it.
CLOCK_SKEW = timedelta(minutes=5)


class AzureStorageError(Exception):
    """Azure Blob Storage is not set up for what was asked."""


def _client() -> BlobServiceClient:
    if not settings.AZURE_STORAGE_CONNECTION_STRING:
        raise AzureStorageError("AZURE_STORAGE_CONNECTION_STRING is not set")
    return BlobServiceClient.from_connection_string(settings.AZURE_STORAGE_CONNECTION_STRING)


async def get_blob_service_client() -> Optional[BlobServiceClient]:
    """Get the async blob service client if configured."""
    if not settings.AZURE_STORAGE_CONNECTION_STRING:
        return None
    return BlobServiceClient.from_connection_string(settings.AZURE_STORAGE_CONNECTION_STRING)


async def store_blob(key: str, data: bytes, metadata: dict | None = None) -> None:
    """Store a binary blob (in-memory bytes) to Azure Blob Storage."""
    client = await get_blob_service_client()
    if not client:
        logger.error("azure_storage_not_configured_for_store")
        # Fallback to saving absolutely nothing, or raise error.
        # Continuing allows failure to bubble up properly.
        raise Exception("Azure Storage is not configured.")

    async with client:
        try:
            container_client = client.get_container_client(settings.AZURE_STORAGE_CONTAINER_NAME)
            if not await container_client.exists():
                await container_client.create_container()

            blob_client = client.get_blob_client(container=settings.AZURE_STORAGE_CONTAINER_NAME, blob=key)
            # Use metadata dictionary if provided; Azure metadata string values must be strings.
            # Azure Blob metadata keys cannot contain underscores so we strip them if present.
            clean_metadata = {k.replace("_", ""): str(v) for k, v in metadata.items()} if metadata else None
            await blob_client.upload_blob(data, metadata=clean_metadata, overwrite=True)
            logger.debug("azure_blob_stored", key=key, size_bytes=len(data))
        except Exception as e:
            logger.error("azure_blob_upload_failed", key=key, error=str(e))
            raise


async def retrieve_blob(key: str) -> tuple[bytes | None, dict | None]:
    """Retrieve a blob and its metadata from Azure Blob Storage."""
    client = await get_blob_service_client()
    if not client:
        logger.error("azure_storage_not_configured_for_retrieve")
        return None, None

    async with client:
        try:
            blob_client = client.get_blob_client(container=settings.AZURE_STORAGE_CONTAINER_NAME, blob=key)
            download_stream = await blob_client.download_blob()
            data = await download_stream.readall()

            # Retrieve metadata
            blob_properties = await blob_client.get_blob_properties()
            metadata = blob_properties.metadata
            return data, metadata
        except Exception as e:
            logger.warning("azure_blob_download_failed", key=key, error=str(e))
            return None, None


async def delete_blob(key: str) -> None:
    """Delete a blob from Azure Blob Storage."""
    client = await get_blob_service_client()
    if not client:
        return

    async with client:
        try:
            blob_client = client.get_blob_client(container=settings.AZURE_STORAGE_CONTAINER_NAME, blob=key)
            await blob_client.delete_blob()
            logger.debug("azure_blob_deleted", key=key)
        except Exception as e:
            logger.warning("azure_blob_delete_failed", key=key, error=str(e))


async def upload_file(container: str, blob_name: str, local_path: Path, content_type: str) -> None:
    """Stream a file from disk into ``container``, replacing a blob of the same name.

    The container is created private when missing. A large file goes up in blocks, a few at a
    time, so a ZIP of several GB is never held in memory. Raises when the upload fails.
    """
    size = local_path.stat().st_size
    async with _client() as client:
        container_client = client.get_container_client(container)
        try:
            await container_client.create_container()
        except ResourceExistsError:
            pass
        with local_path.open("rb") as fh:
            await container_client.upload_blob(
                blob_name,
                fh,
                length=size,
                overwrite=True,
                content_settings=ContentSettings(content_type=content_type),
                max_concurrency=UPLOAD_CONCURRENCY,
            )
    logger.info("azure_file_stored", container=container, blob=blob_name, size_bytes=size)


def read_link(container: str, blob_name: str, ttl_seconds: int, filename: str) -> str:
    """A read-only HTTPS link to one blob that works for ``ttl_seconds`` and saves as ``filename``.

    Signed with the account key in the connection string, so anyone with the link can read
    that one blob until it expires, without signing in. Raises when there is no key to sign with.
    """
    client = _client()
    account_name, account_key = client.account_name, getattr(client.credential, "account_key", None)
    if not account_name or not account_key:
        raise AzureStorageError("The connection string has no AccountName and AccountKey to sign a link with")
    now = datetime.now(timezone.utc)
    sas = generate_blob_sas(
        account_name=account_name,
        container_name=container,
        blob_name=blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        start=now - CLOCK_SKEW,
        expiry=now + timedelta(seconds=ttl_seconds),
        protocol="https",
        content_disposition=f'attachment; filename="{filename}"',
    )
    return f"{client.get_blob_client(container, blob_name).url}?{sas}"
