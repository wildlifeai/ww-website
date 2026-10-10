# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""EXIF parsing domain — full port from exif_parser.py.

Browser-side (exifr) handles most parsing; this is the server-side
fallback for custom EXIF tags produced by the camera firmware.
"""

import io
import re
import struct
from typing import Any, Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger()

# ── EXIF tag IDs and names ───────────────────────────────────────────

EXIF_TAGS = {
    0x010F: "Make",  # WW500 writes "Wildlife.ai"
    0x0110: "Model",  # WW500 writes "WW500 RP3" / "WW500 HM0360" (bare "WW500" pre camera-variant firmware)
    0x0131: "Software",  # WW500 firmware build string, e.g. "WW500_C02 09:16:17 Jul  7 2026"
    0x0132: "DateTime",
    0x9003: "Datetime_Original",
    0x9004: "Datetime_Create",
    0x9209: "Flash",  # standard EXIF Flash: bit0 = flash/IR illumination fired
    0x927C: "MakerNote",  # WW500 capture settings CSV (AE regs [+ WB gains + flash on newer firmware])
    0x9286: "UserComment",  # WW500 on-device NN scores + telemetry, "label: value; ..."
    0xC000: "Custom_Data",  # WW500 raw NN output tensor
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

# Firmware UserComment is a "label: value; label: value; " string carrying the
# on-device NN class scores and (when enabled) device telemetry. These keys are
# surfaced as typed top-level fields; everything is also kept in
# ``user_comment_fields`` so the on-device AI scores are preserved.
_TELEMETRY_KEYS = {
    "temp": ("temperature_c", float),
    "temperature": ("temperature_c", float),
    "batt": ("battery_pct", int),
    "battery": ("battery_pct", int),
    "rssi": ("lorawan_rssi", int),
    "snr": ("lorawan_snr", float),
}

TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 8: 2, 9: 4, 10: 8}

# struct codes for the integer types: SHORT, LONG, SSHORT, SLONG.
_INT_FORMATS = {3: "H", 4: "I", 8: "h", 9: "i"}

# The EXIF Make every WW500 writes, and the id a camera with no deployment set
# could carry. Together they identify a test photo (see is_test_photo).
WW500_MAKE = "Wildlife.ai"
ZERO_DEPLOYMENT_ID = "00000000-0000-0000-0000-000000000000"


# ── Low-level EXIF parsing ───────────────────────────────────────────


def _format_value(value: bytes, type_id: int, endian: str = "<"):
    """Convert raw EXIF bytes to a Python-friendly value.

    ``endian`` is the file's byte order from the TIFF header, ``"<"`` (II) or
    ``">"`` (MM). WW500 frames are little-endian; many other cameras write MM.
    """
    if isinstance(value, bytes):
        if type_id == 2:  # ASCII
            try:
                return value.decode("ascii", errors="replace").strip("\x00")
            except Exception:
                return None
        elif type_id in (5, 10):  # RATIONAL or SRATIONAL
            fmt = endian + ("II" if type_id == 5 else "ii")
            pairs = []
            for i in range(0, len(value), 8):
                if i + 8 > len(value):
                    break
                num, denom = struct.unpack(fmt, value[i : i + 8])
                pairs.append(num / denom if denom != 0 else 0.0)
            return pairs[0] if len(pairs) == 1 else pairs
        elif type_id in _INT_FORMATS:  # SHORT, LONG, SSHORT, SLONG: first value only
            fmt = endian + _INT_FORMATS[type_id]
            size = TYPE_SIZES[type_id]
            try:
                return struct.unpack(fmt, value[:size])[0] if len(value) >= size else value.hex()
            except Exception:
                return value.hex()
        elif type_id in (1, 7):  # BYTE or UNDEFINED
            try:
                decoded = value.decode("ascii", errors="ignore").strip("\x00")
                if any(c.isalnum() for c in decoded):
                    return decoded
            except Exception:
                pass
            return value.hex()

        # Fallback for any unhandled byte types: Convert to hex string to prevent JSON serialization crashes
        return value.hex()
    return value


def _parse_ifd(
    fp,
    base_offset: int,
    ifd_offset: int,
    endian: str,
    parsed_data: Dict[str, Any],
    check_next_ifd: bool = True,
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
            parsed_data[tag_name] = _format_value(value, type_id, endian)

        # Auto-follow pointer tags into sub-IFDs, then come back for the next
        # entry. Cameras that sort their tags put ExifIFDPointer before
        # GPSInfoIFDPointer, so without the seek the GPS IFD was lost.
        if tag in (0x8769, 0x8825):  # ExifIFDPointer, GPSInfoIFDPointer
            current_pos = fp.tell()
            _parse_ifd(fp, base_offset, value_offset, endian, parsed_data, check_next_ifd=False)
            fp.seek(current_pos)

    # Parse next IFD in the chain (if any)
    if check_next_ifd:
        next_ifd = fp.read(4)
        if len(next_ifd) == 4:
            next_ifd_offset = struct.unpack(endian + "I", next_ifd)[0]
            if next_ifd_offset != 0:
                _parse_ifd(fp, base_offset, next_ifd_offset, endian, parsed_data)


# ── Public API ───────────────────────────────────────────────────────


def _strip_nul(obj: Any) -> Any:
    """Recursively drop NUL (``\\x00``) bytes from strings in a parsed-EXIF value.

    EXIF ``UserComment`` carries an 8-byte NUL-padded charset code
    (``"ASCII\\x00\\x00\\x00…"``), and other tags can hold stray NULs. Postgres
    ``jsonb``/``text`` reject ``\\u0000`` (SQLSTATE 22P05), so an unsanitised
    ``exif_metadata`` fails media registration for every such image — including
    real WW500 frames once the firmware emits UserComment. Sanitise here so every
    consumer of the parsed dict is safe.
    """
    if isinstance(obj, str):
        return obj.replace("\x00", "")
    if isinstance(obj, dict):
        return {(k.replace("\x00", "") if isinstance(k, str) else k): _strip_nul(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_nul(v) for v in obj]
    return obj


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

    # Parse the firmware UserComment "label: value; ..." payload (on-device NN
    # scores + telemetry) into structured fields.
    _apply_user_comment_fields(parsed_data)

    # Camera variant, capture settings (MakerNote CSV) and flash state — written
    # by the camera-variant-aware firmware; absent on older images.
    _apply_camera_fields(parsed_data)

    # Extract deployment ID from custom firmware EXIF tags
    deployment_id = _extract_deployment_id(parsed_data)
    parsed_data["deployment_id"] = deployment_id

    # Sanitise NUL bytes (from the UserComment charset prefix, etc.) so the dict
    # is safe to store as jsonb — otherwise media registration fails with 22P05.
    return _strip_nul(parsed_data)


def parse_maker_note_fields(maker_note: Any) -> Dict[str, Any]:
    """Parse the WW500 MakerNote capture-settings CSV into typed fields.

    Legacy firmware writes 5 fields::

        "<integration>, <analogGain>, <digitalGain>, <aeMean>, <Y|N>"

    Camera-variant firmware appends 3 more (backward-compatible)::

        "..., <wbRedGain>, <wbBlueGain>, <flashFired>"

    Returns ``{}`` for anything that does not look like the WW500 CSV.
    """
    if not maker_note or not isinstance(maker_note, str):
        return {}
    parts = [p.strip() for p in maker_note.replace("\x00", "").split(",")]
    if len(parts) < 5:
        return {}
    try:
        fields: Dict[str, Any] = {
            "integration_lines": int(parts[0]),
            "analog_gain": int(parts[1]),
            "digital_gain": int(parts[2]),
            "ae_mean": int(parts[3]),
            "ae_converged": parts[4].upper() == "Y",
        }
    except (TypeError, ValueError):
        return {}
    if len(parts) >= 8:
        try:
            # Parse into temporaries first so a malformed extension is ignored
            # atomically (never a partial wb-without-flash state).
            wb_red = int(parts[5])
            wb_blue = int(parts[6])
            flash = bool(int(parts[7]))
        except (TypeError, ValueError):
            pass  # keep the AE fields; ignore a malformed extension
        else:
            fields["wb_red_gain"] = wb_red
            fields["wb_blue_gain"] = wb_blue
            fields["flash_fired"] = flash
    return fields


def _camera_variant_from_model(model: Any) -> Optional[str]:
    """Map the EXIF Model string to the firmware camera_variant nomenclature.

    "WW500 RP3" -> "RP3", "WW500 HM0360" -> "HM0360", "WW500 RP2" -> "RP2".
    Bare "WW500" (pre-variant firmware) and non-WW500 models -> None.
    """
    if not model or not isinstance(model, str):
        return None
    parts = model.replace("\x00", "").strip().split()
    if len(parts) >= 2 and parts[0].upper() == "WW500":
        variant = parts[1].upper()
        if variant in ("RP3", "HM0360", "RP2"):
            return variant
    return None


def _apply_camera_fields(parsed_data: Dict[str, Any]) -> None:
    """Surface camera variant, capture settings and flash state as typed fields."""
    variant = _camera_variant_from_model(parsed_data.get("Model"))
    if variant:
        parsed_data["camera_variant"] = variant

    # MakerNote capture-settings CSV (AE registers [+ WB gains + flash])
    for key, val in parse_maker_note_fields(parsed_data.get("MakerNote")).items():
        parsed_data.setdefault(key, val)

    # Standard EXIF Flash tag (bit0 = fired) takes precedence over the
    # MakerNote copy when both are present.
    flash_raw = parsed_data.get("Flash")
    if isinstance(flash_raw, int):
        parsed_data["flash_fired"] = bool(flash_raw & 1)


def parse_user_comment_fields(user_comment: Any) -> Dict[str, str]:
    """Parse the WW500 ``"label: value; label: value; "`` UserComment into a dict.

    The firmware packs on-device NN class scores (e.g. ``"kiwi: 80; rat: 12;"``)
    and, when telemetry is enabled, fields like ``"Temp: 14.5; Batt: 87;"``.
    Returns ``{label: value}`` for every ``key: value`` token; non-conforming
    UserComments (e.g. a bare deployment UUID) yield ``{}``.
    """
    fields: Dict[str, str] = {}
    if not user_comment or not isinstance(user_comment, str):
        return fields
    # An EXIF UserComment carries an 8-byte character-code prefix ("ASCII\0\0\0",
    # "UNICODE\0", "JIS\0\0\0\0\0"). The all-zero (undefined) prefix is already
    # dropped by null-stripping, but the named ones start with a letter and survive
    # decoding as e.g. "ASCII\0\0\0kiwi: 80;…" — strip the prefix (only when it's
    # followed by the spec's null padding, so a real key can't be mis-stripped) plus
    # any stray interior nulls, so the first key isn't corrupted.
    user_comment = re.sub(r"^(?:ASCII|UNICODE|JIS|UNDEFINED)\x00+", "", user_comment).replace("\x00", "")
    for part in user_comment.split(";"):
        if ":" not in part:
            continue
        key, _, val = part.partition(":")
        key, val = key.strip(), val.strip()
        if key and val:
            fields[key] = val
    return fields


def _apply_user_comment_fields(parsed_data: Dict[str, Any]) -> None:
    """Surface UserComment key/values + typed telemetry onto ``parsed_data``."""
    fields = parse_user_comment_fields(parsed_data.get("UserComment"))
    if not fields:
        return
    parsed_data["user_comment_fields"] = fields
    for raw_key, val in fields.items():
        mapping = _TELEMETRY_KEYS.get(raw_key.strip().lower())
        if not mapping:
            continue
        target, caster = mapping
        if target in parsed_data:
            continue
        try:
            parsed_data[target] = caster(float(val)) if caster is int else caster(val)
        except (TypeError, ValueError):
            pass


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


def is_test_photo(parsed: Dict[str, Any]) -> bool:
    """True for a WW500 test photo, which is never uploaded (ww-website#287).

    The firmware writes the ``Deployment_ID`` tag only once a deployment is set,
    so a frame whose ``Make`` is ``"Wildlife.ai"`` and whose EXIF carries no
    deployment id, or the all-zero one, was taken on the bench before that. The
    card folder plays no part: such frames sit in ``MEDIA/00000000/``, but so
    can real ones taken right after the id was set. A file with no EXIF (a BMP
    frame, another camera) is not a test photo. ``parsed`` is the output of
    :func:`parse_exif_from_bytes`. Mirrors ``isTestPhoto`` in
    ``frontend/src/lib/exifDeploymentId.ts``.
    """
    make = parsed.get("Make")
    if not isinstance(make, str) or make.strip().lower() != WW500_MAKE.lower():
        return False
    dep_id = parsed.get("deployment_id")
    return not dep_id or str(dep_id).lower() == ZERO_DEPLOYMENT_ID


def resolve_deployment_source(exif_deployment_id: Optional[str], folder_prefix: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Which deployment id a frame binds to, and where it came from.

    The camera stamps the configured deployment's full UUID into EXIF tag 0xF200
    (``Deployment_ID``). The card folder ``MEDIA/<8-hex>/`` holds only a prefix
    of the same id and is created at boot, before the id is configured, so a
    frame can sit under ``MEDIA/00000000/`` while its EXIF names the real
    deployment. The tag therefore wins; the folder fills in only when there is
    no EXIF id at all (a BMP frame, or an image from something other than a
    WW500). A WW500 frame without the tag is a test photo and never reaches
    this function: callers drop it first with :func:`is_test_photo`
    (ww-website#287).

    Returns ``(id, source)`` with ``source`` one of ``"exif_tag"``,
    ``"folder_path"`` or ``None`` when neither is present. The id keeps the
    caller's form: a full UUID from EXIF, an upper-cased 8-hex prefix from the
    folder, so downstream can still tell them apart by length.
    (ww-website#140)
    """
    if exif_deployment_id:
        return exif_deployment_id, "exif_tag"
    if folder_prefix:
        return folder_prefix.upper(), "folder_path"
    return None, None
