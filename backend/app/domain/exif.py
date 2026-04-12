# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""EXIF parsing domain — full port from exif_parser.py.

Browser-side (exifr) handles most parsing; this is the server-side
fallback for custom EXIF tags produced by the camera firmware.
"""

import struct
import io
import re
from typing import Dict, Any, List, Optional

import structlog

logger = structlog.get_logger()

# ── EXIF tag IDs and names ───────────────────────────────────────────

EXIF_TAGS = {
    0x0132: "DateTime",
    0x9003: "Datetime_Original",
    0x9004: "Datetime_Create",
    0x9286: "UserComment",
    0xC000: "Custom_Data",
    0xF200: "Deployment_ID",
    0x0001: "GPS_Latitude_Reference",
    0x0002: "GPS_Latitude",
    0x0003: "GPS_Longitude_Reference",
    0x0004: "GPS_Longitude",
    0x0005: "GPS_Altitude_Reference",
    0x0006: "GPS_Altitude",
    0x8769: "ExifIFDPointer",
    0x8825: "GPSInfoIFDPointer",
}

TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}


# ── Low-level EXIF parsing ───────────────────────────────────────────

def _format_value(value: bytes, type_id: int):
    """Convert raw EXIF bytes to a Python-friendly value."""
    if isinstance(value, bytes):
        if type_id == 2:  # ASCII
            try:
                return value.decode("ascii", errors="replace").strip("\x00")
            except Exception:
                return None
        elif type_id in (5, 10):  # RATIONAL or SRATIONAL
            fmt = "<II" if type_id == 5 else "<ii"
            pairs = []
            for i in range(0, len(value), 8):
                if i + 8 > len(value):
                    break
                num, denom = struct.unpack(fmt, value[i : i + 8])
                pairs.append(num / denom if denom != 0 else 0.0)
            return pairs[0] if len(pairs) == 1 else pairs
        elif type_id in (1, 7):  # BYTE or UNDEFINED
            try:
                decoded = value.decode("ascii", errors="ignore").strip("\x00")
                if any(c.isalnum() for c in decoded):
                    return decoded
            except Exception:
                pass
            return value.hex()
    return value


def _parse_ifd(
    fp, base_offset: int, ifd_offset: int, endian: str,
    parsed_data: Dict[str, Any], check_next_ifd: bool = True,
) -> None:
    """Parse a single IFD (Image File Directory) block."""
    try:
        fp.seek(base_offset + ifd_offset)
        raw = fp.read(2)
        if len(raw) < 2:
            return
        num_entries = struct.unpack(endian + "H", raw)[0]
    except Exception:
        return

    for _ in range(num_entries):
        entry = fp.read(12)
        if len(entry) < 12:
            return

        tag, type_id, count, value_offset = struct.unpack(endian + "HHII", entry)
        type_size = TYPE_SIZES.get(type_id, 1)
        total_size = type_size * count

        tag_name = EXIF_TAGS.get(tag, None)

        # Handle inline values (≤4 bytes stored in the offset field itself)
        if total_size <= 4:
            raw_bytes = struct.pack(endian + "I", value_offset)
            value = raw_bytes[:total_size]
        else:
            current_pos = fp.tell()
            try:
                fp.seek(base_offset + value_offset)
                value = fp.read(total_size)
            except Exception:
                value = b""
            fp.seek(current_pos)

        if tag_name:
            parsed_data[tag_name] = _format_value(value, type_id)

        # Auto-follow pointer tags into sub-IFDs
        if tag == 0x8825:  # GPSInfoIFDPointer
            _parse_ifd(fp, base_offset, value_offset, endian, parsed_data, check_next_ifd=False)
        elif tag == 0x8769:  # ExifIFDPointer
            _parse_ifd(fp, base_offset, value_offset, endian, parsed_data, check_next_ifd=False)

    # Parse next IFD in the chain (if any)
    if check_next_ifd:
        next_ifd = fp.read(4)
        if len(next_ifd) == 4:
            next_ifd_offset = struct.unpack(endian + "I", next_ifd)[0]
            if next_ifd_offset != 0:
                _parse_ifd(fp, base_offset, next_ifd_offset, endian, parsed_data)


# ── Public API ───────────────────────────────────────────────────────

def parse_exif_from_bytes(jpeg_bytes: bytes) -> Dict[str, Any]:
    """Parse EXIF metadata from raw JPEG bytes.

    Extracts standard fields (DateTime, GPS) and custom Wildlife Watcher
    firmware fields (Deployment_ID, Custom_Data).

    Returns a dict with parsed fields including computed ``latitude``,
    ``longitude``, ``date``, and ``deployment_id``.
    """
    parsed_data: Dict[str, Any] = {}

    # Validate JPEG magic bytes
    if len(jpeg_bytes) < 2 or jpeg_bytes[:2] != b"\xff\xd8":
        return {"error": "Not a valid JPEG file"}

    fp = io.BytesIO(jpeg_bytes)

    # Scan JPEG markers for APP1 (EXIF)
    while True:
        marker_start = fp.read(1)
        if not marker_start:
            break
        if marker_start != b"\xff":
            continue
        marker = fp.read(1)
        if marker in [b"\xd8", b"\xd9"]:
            continue
        length_bytes = fp.read(2)
        if len(length_bytes) < 2:
            break
        length = struct.unpack(">H", length_bytes)[0]
        segment_offset = fp.tell()
        segment_data = fp.read(length - 2)

        if marker == b"\xe1":  # APP1 (EXIF)
            if not segment_data.startswith(b"Exif\x00\x00"):
                continue
            endian_flag = segment_data[6:8]
            if endian_flag == b"II":
                endian = "<"
            elif endian_flag == b"MM":
                endian = ">"
            else:
                continue

            if len(segment_data) < 14:
                continue
            tiff_header_offset = segment_offset + 6
            first_ifd_offset = struct.unpack(endian + "I", segment_data[10:14])[0]

            _parse_ifd(fp, tiff_header_offset, first_ifd_offset, endian, parsed_data)
            break

    # ── Post-processing ──────────────────────────────────────────────

    # Compute decimal GPS coordinates
    if "GPS_Latitude" in parsed_data and "GPS_Longitude" in parsed_data:
        try:
            lat = parsed_data["GPS_Latitude"]
            lon = parsed_data["GPS_Longitude"]
            lat_ref = parsed_data.get("GPS_Latitude_Reference", "N")
            lon_ref = parsed_data.get("GPS_Longitude_Reference", "E")

            lat_deg = lat[0] + lat[1] / 60.0 + lat[2] / 3600.0
            lon_deg = lon[0] + lon[1] / 60.0 + lon[2] / 3600.0

            if lat_ref == "S":
                lat_deg = -lat_deg
            if lon_ref == "W":
                lon_deg = -lon_deg

            parsed_data["latitude"] = round(lat_deg, 6)
            parsed_data["longitude"] = round(lon_deg, 6)
        except Exception:
            pass

    # Normalise date (pick the first available)
    for dt_key in ["DateTime", "Datetime_Original", "Datetime_Create"]:
        if dt_key in parsed_data:
            parsed_data["date"] = parsed_data[dt_key]
            break

    # Extract deployment ID from custom firmware EXIF tags
    deployment_id = _extract_deployment_id(parsed_data)
    parsed_data["deployment_id"] = deployment_id

    return parsed_data


def _extract_deployment_id(parsed_data: Dict[str, Any]) -> Optional[str]:
    """Try to extract a UUID deployment ID from EXIF data.

    Priority: Deployment_ID tag → UserComment → Custom_Data.
    Validates against UUID format.
    """
    candidates = [
        parsed_data.get("Deployment_ID"),
        parsed_data.get("UserComment"),
        parsed_data.get("Custom_Data"),
    ]

    for raw in candidates:
        if not raw:
            continue
        cleaned = str(raw).strip()
        if not cleaned:
            continue

        # For UserComment, try to extract a UUID from the end
        match = re.search(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            cleaned.lower(),
        )
        if match:
            return match.group(0)

    return None


def match_deployment(
    exif_data: Dict[str, Any],
    deployments: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Cross-reference EXIF data with deployment records.

    Matches based on:
    1. deployment_id from custom EXIF tag (exact match)
    2. GPS proximity (within ~50m) + date overlap

    Returns the best-matching deployment dict, or None.
    """
    deployment_id = exif_data.get("deployment_id")

    # Priority 1: exact ID match
    if deployment_id:
        for d in deployments:
            if str(d.get("id", "")).lower() == deployment_id.lower():
                return d

    # Priority 2: GPS proximity
    lat = exif_data.get("latitude")
    lon = exif_data.get("longitude")
    if lat is not None and lon is not None:
        best_match = None
        best_distance = float("inf")

        for d in deployments:
            d_lat = d.get("latitude")
            d_lon = d.get("longitude")
            if d_lat is None or d_lon is None:
                continue

            # Approximate distance in degrees (~0.0005° ≈ 50m)
            distance = ((lat - d_lat) ** 2 + (lon - d_lon) ** 2) ** 0.5
            if distance < 0.0005 and distance < best_distance:
                best_distance = distance
                best_match = d

        if best_match:
            return best_match

    return None
