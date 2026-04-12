# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""EXIF parsing domain — extracted from exif_parser.py.

Browser-side (exifr) handles most parsing; this is the server-side
fallback for custom EXIF tags produced by the camera firmware.
"""

import struct
from typing import Dict, Any, List, Optional


def parse_exif_from_bytes(jpeg_bytes: bytes) -> Dict[str, Any]:
    """Parse EXIF metadata from JPEG bytes.

    This is a direct port of exif_parser.py parse_exif() for server-side use.
    The frontend uses exifr for browser-side parsing and falls back here
    for custom firmware EXIF tags.

    Args:
        jpeg_bytes: Raw JPEG file content.

    Returns:
        Dict of parsed EXIF fields (GPS, timestamps, custom WW fields).
    """
    # Placeholder — full implementation will be ported from exif_parser.py
    # in a follow-up when the domain tests are written
    result: Dict[str, Any] = {}

    # Validate JPEG magic bytes
    if len(jpeg_bytes) < 2 or jpeg_bytes[:2] != b"\xff\xd8":
        return {"error": "Not a valid JPEG file"}

    # TODO: Port full EXIF extraction from exif_parser.py
    # Including: GPS coords, DateTime, custom WW deployment ID tag

    return result


def match_deployment(
    exif_data: Dict[str, Any],
    deployments: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Cross-reference EXIF data with deployment records.

    Matches based on GPS coordinates and/or deployment ID embedded
    in custom EXIF tags.
    """
    # TODO: Port matching logic from app.py L1842-1950
    return None
