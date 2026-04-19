# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Azure Blob Storage adapter for temporary image buffering.

Replaces the local temp folder blob store to allow
distributed buffering across Container Apps.
"""

import asyncio
from typing import Optional

import structlog
from azure.storage.blob.aio import BlobServiceClient
from app.config import settings

logger = structlog.get_logger()

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

            blob_client = client.get_blob_client(
                container=settings.AZURE_STORAGE_CONTAINER_NAME,
                blob=key
            )
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
            blob_client = client.get_blob_client(
                container=settings.AZURE_STORAGE_CONTAINER_NAME,
                blob=key
            )
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
            blob_client = client.get_blob_client(
                container=settings.AZURE_STORAGE_CONTAINER_NAME,
                blob=key
            )
            await blob_client.delete_blob()
            logger.debug("azure_blob_deleted", key=key)
        except Exception as e:
            logger.warning("azure_blob_delete_failed", key=key, error=str(e))
