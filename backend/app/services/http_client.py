# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""HTTP client with automatic retries and exponential backoff.

Wraps httpx with tenacity for resilient external calls (GitHub downloads,
SSCMA catalog, etc.).
"""

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


class DownloadError(Exception):
    """Raised when a download fails after all retries."""

    pass


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
)
async def download_with_retry(url: str, timeout: float = 30.0) -> bytes:
    """Download binary content from a URL with retries.

    Args:
        url: The URL to download from.
        timeout: Per-request timeout in seconds.

    Returns:
        Response body as bytes.

    Raises:
        DownloadError: If all retries are exhausted.
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content


async def download_url_content(url: str) -> bytes:
    """High-level download wrapper matching app.py's interface."""
    try:
        return await download_with_retry(url)
    except Exception as e:
        raise DownloadError(f"Failed to download {url}: {e}") from e
