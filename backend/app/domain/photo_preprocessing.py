# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Photo preprocessing — rename files and build Drive folder names.

Transforms raw SD card images into human-readable, ecologically meaningful
names before uploading to Google Drive.

Folder convention::

    <root>
    ├── {project_name}_{project_id[:8]}
    │   └── {YYYYMMDD}_{duration}_{location}
    │       ├── 20260113103000_01.jpg
    │       └── 20260113103000_02.jpg   (second capture in same second)

Duration format: ``XdYhZmWs`` (e.g. ``2d21h41m22s``).
If the deployment is still active, duration is ``ongoing``.

Filenames use **local time** derived from GPS coordinates via
``timezonefinder``.  Falls back to UTC when GPS is unavailable.
"""

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import structlog

from app.services.google_drive import slugify

logger = structlog.get_logger()

# Lazy-loaded singletons
_tf = None


def _get_timezone_finder():
    """Lazy-load TimezoneFinder (expensive first import ~50MB dataset)."""
    global _tf
    if _tf is None:
        from timezonefinder import TimezoneFinder

        _tf = TimezoneFinder()
    return _tf


# ── UTC → Local Time ────────────────────────────────────────────────


def utc_to_local(utc_dt: datetime, lat: float, lon: float) -> datetime:
    """Convert a UTC datetime to local time using GPS coordinates.

    Uses ``timezonefinder`` to resolve lat/lon → IANA timezone, then
    applies the offset.  Returns the original datetime unchanged if
    the timezone cannot be determined.
    """
    try:
        from zoneinfo import ZoneInfo

        tf = _get_timezone_finder()
        tz_name = tf.timezone_at(lat=lat, lng=lon)
        if not tz_name:
            logger.debug("timezone_not_found", lat=lat, lon=lon)
            return utc_dt
        local_tz = ZoneInfo(tz_name)
        return utc_dt.astimezone(local_tz)
    except Exception as exc:
        logger.warning("timezone_conversion_failed", error=str(exc))
        return utc_dt


def parse_exif_timestamp(ts: str) -> Optional[datetime]:
    """Parse an EXIF-style timestamp into a UTC datetime.

    Handles formats:
    - ``YYYY:MM:DD HH:MM:SS`` (standard EXIF)
    - ``YYYY-MM-DD HH:MM:SS``
    - ``YYYY-MM-DDTHH:MM:SS``
    """
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(ts.strip()[:19], fmt)
            return dt.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            continue
    return None


# ── Deployment Folder Name ──────────────────────────────────────────


def _format_duration(start_iso: str, end_iso: Optional[str]) -> str:
    """Compute human-readable duration string from ISO timestamps.

    Returns ``XdYhZmWs`` or ``ongoing`` if end is None/empty.
    """
    if not end_iso:
        return "ongoing"

    try:
        start = datetime.fromisoformat(start_iso)
        end = datetime.fromisoformat(end_iso)
        delta = end - start
        total_secs = int(delta.total_seconds())
        if total_secs <= 0:
            return "ongoing"

        days = total_secs // 86400
        hours = (total_secs % 86400) // 3600
        mins = (total_secs % 3600) // 60
        secs = total_secs % 60
        return f"{days}d{hours}h{mins}m{secs}s"
    except Exception:
        return "ongoing"


def _sanitize_location(name: str) -> str:
    """Convert a location name to a folder-safe slug.

    Lowercase, strip non-alphanumeric characters, no spaces.
    """
    slug = re.sub(r"[^a-z0-9]", "", name.lower())
    return slug if slug else "unknown"


def build_deployment_folder_name(
    deployment_start: Optional[str],
    deployment_end: Optional[str],
    location_name: Optional[str],
) -> str:
    """Build the deployment folder name.

    Format: ``YYYYMMDD_XdYhZmWs_locationname``

    Examples:
        - ``20260113_2d21h41m22s_highhill``
        - ``20260113_ongoing_highhill``
        - ``20260113_0d5h30m0s_unknown``
    """
    if deployment_start:
        try:
            start_dt = datetime.fromisoformat(deployment_start)
            start_date = start_dt.strftime("%Y%m%d")
        except Exception:
            start_date = datetime.now(timezone.utc).strftime("%Y%m%d")
    else:
        start_date = datetime.now(timezone.utc).strftime("%Y%m%d")

    duration = _format_duration(deployment_start or "", deployment_end)
    location = _sanitize_location(location_name or "")

    return f"{start_date}_{duration}_{location}"


# ── Photo Filename ──────────────────────────────────────────────────


def build_photo_filename(
    utc_timestamp: str,
    lat: Optional[float],
    lon: Optional[float],
    sequence: int = 1,
) -> str:
    """Build a photo filename from a UTC timestamp and GPS coordinates.

    Format: ``YYYYMMDDHHMMSS_XX.jpg``

    The timestamp is converted to local time using GPS coordinates.
    Falls back to UTC when coordinates are unavailable.

    Parameters
    ----------
    utc_timestamp : str
        EXIF-style timestamp (e.g. ``2026:01:13 10:30:00``).
    lat, lon : float or None
        GPS coordinates for timezone resolution.
    sequence : int
        1-indexed sequence number for photos in the same second.

    Returns
    -------
    str
        Filename like ``20260113103000_01.jpg``.
    """
    utc_dt = parse_exif_timestamp(utc_timestamp)
    if not utc_dt:
        # Can't parse — return a safe fallback
        return f"unknown_{sequence:02d}.jpg"

    if lat is not None and lon is not None:
        local_dt = utc_to_local(utc_dt, lat, lon)
    else:
        local_dt = utc_dt

    return f"{local_dt.strftime('%Y%m%d%H%M%S')}_{sequence:02d}.jpg"


# ── Batch Preprocessing ────────────────────────────────────────────


def preprocess_file_batch(
    files: List[Dict[str, Any]],
    deployment: Dict[str, Any],
    project: Dict[str, Any],
) -> Tuple[str, str, List[Dict[str, Any]]]:
    """Preprocess a batch of files for one deployment.

    1. Sorts files by timestamp for deterministic sequence numbering.
    2. Assigns sequence numbers for same-second captures.
    3. Builds a ``drive_filename`` for each file.
    4. Returns the computed deployment folder name and project folder name.

    Parameters
    ----------
    files : list of dicts
        Each dict must have ``filename``, ``timestamp``, and optionally
        ``latitude``/``longitude`` from EXIF.
    deployment : dict
        Must have ``deployment_start``, optionally ``deployment_end``,
        ``location_name``, ``latitude``, ``longitude``.
    project : dict
        Must have ``name`` and ``id``.

    Returns
    -------
    (deployment_folder_name, project_folder_name, files)
        Files are modified in-place with ``drive_filename`` added.
    """
    dep_lat = deployment.get("latitude")
    dep_lon = deployment.get("longitude")

    # Sort by timestamp for predictable sequence assignment
    files.sort(key=lambda f: f.get("timestamp") or "")

    # Pass 1: count how many photos share each second
    second_cursors: Dict[str, int] = {}

    for f in files:
        ts = f.get("timestamp")
        if not ts:
            # No timestamp — keep original filename
            f["drive_filename"] = f.get("filename", "unknown.jpg")
            continue

        second_key = ts.strip()[:19]  # "YYYY:MM:DD HH:MM:SS"
        seq = second_cursors.get(second_key, 0) + 1
        second_cursors[second_key] = seq

        # Use per-image GPS if available, otherwise deployment GPS
        img_lat = f.get("latitude") or dep_lat
        img_lon = f.get("longitude") or dep_lon

        f["drive_filename"] = build_photo_filename(ts, img_lat, img_lon, seq)

    # Build folder names
    dep_folder = build_deployment_folder_name(
        deployment.get("deployment_start"),
        deployment.get("deployment_end"),
        deployment.get("location_name"),
    )

    proj_name = project.get("name", "unknown")
    proj_id = project.get("id", "00000000")
    project_folder = f"{slugify(proj_name)}_{proj_id[:8]}"

    logger.info(
        "photo_batch_preprocessed",
        file_count=len(files),
        deployment_folder=dep_folder,
        project_folder=project_folder,
    )

    return dep_folder, project_folder, files
