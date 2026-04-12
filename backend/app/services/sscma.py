# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""SSCMA Model Zoo service.

Fetches and caches the official Seeed Studio Model Assistant catalog.
"""

import json
from typing import List, Dict, Any

from app.services.cache import cached
from app.services.http_client import download_url_content

SSCMA_URL = "https://raw.githubusercontent.com/Seeed-Studio/sscma-model-zoo/main/models.json"


async def get_sscma_catalog() -> List[Dict[str, Any]]:
    """Return cached SSCMA model zoo catalog (1 hour TTL)."""
    
    async def fetch_catalog():
        content = await download_url_content(SSCMA_URL)
        data = json.loads(content)
        return data.get("models", [])

    return await cached("sscma:catalog", ttl=3600, fetch_fn=fetch_catalog)


async def get_sscma_model(uuid: str) -> Dict[str, Any]:
    """Get a specific SSCMA model by UUID."""
    catalog = await get_sscma_catalog()
    for model in catalog:
        if model.get("uuid") == uuid:
            return model
    raise ValueError(f"SSCMA model {uuid} not found")
