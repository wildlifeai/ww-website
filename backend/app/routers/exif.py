# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""EXIF parsing endpoint — sync (small payload, fast response).

POST /api/exif/parse — accepts uploaded JPEG files, returns parsed EXIF data.
Frontend uses exifr browser-side by default; this is the server-side fallback
for custom firmware EXIF tags.
"""

from fastapi import APIRouter, UploadFile, File, Request
from typing import List

from app.schemas.common import ApiResponse, ApiMeta
from app.domain.exif import parse_exif_from_bytes

router = APIRouter(prefix="/api/exif", tags=["exif"])


@router.post("/parse")
async def parse_exif(
    files: List[UploadFile] = File(...),
    request: Request = None,
):
    """Parse EXIF metadata from one or more uploaded JPEG files."""
    results = []

    for upload in files:
        content = await upload.read()
        parsed = parse_exif_from_bytes(content)
        results.append(
            {
                "filename": upload.filename,
                "exif": parsed,
            }
        )

    return ApiResponse(
        data=results,
        meta=ApiMeta(
            request_id=getattr(request.state, "request_id", None) if request else None
        ),
    )
