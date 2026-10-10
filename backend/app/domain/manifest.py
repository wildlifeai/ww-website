# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Manifest generation domain — ported from app.py L721-852.

Orchestrates: fetch config firmware → fetch AI model → fetch Himax firmware → assemble MANIFEST.zip.
Reusable by both the API handler and the async ARQ worker.

The MANIFEST.zip is what gets deployed to the camera SD card. Structure:
    MANIFEST/
    ├── CONFIG.TXT          # Camera configuration
    ├── README.TXT          # SD card setup instructions
    ├── CONFIG.MD           # Operational parameters documentation (8.3 format)
    ├── {fw_id}V{ver}.TFL   # AI model binary (8.3 format)
    ├── {fw_id}V{ver}.TXT   # Model labels (8.3 format)
    └── YYMDDHMM.IMG        # Himax coprocessor firmware (8.3 date-encoded)
"""

# ── GitHub repo constants ────────────────────────────────────────────
import asyncio
import calendar
import io
import json
import re
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog

from app.registries.camera_configs import CAMERA_CONFIGS
from app.registries.model_registry import get_model_config
from app.services.cache import cached
from app.services.db_utils import rows_of
from app.services.http_client import DownloadError, download_url_content
from app.services.storage import download_from_storage
from app.services.supabase_client import create_service_client

DEFAULT_FIRMWARE_BRANCHES = ["main", "dev", "firmware_updates", "live_video", "ledflash2"]

GROVE_VISION_REPO = "wildlifeai/Seeed_Grove_Vision_AI_Module_V2"
MANIFEST_BASE = "EPII_CM55M_APP_S/app/ww_projects/ww500_md/MANIFEST"

_GITHUB_MANIFEST_FILES = {
    "CONFIG.TXT": f"{MANIFEST_BASE}/CONFIG.TXT",
    "README.TXT": f"{MANIFEST_BASE}/README.TXT",
    "CONFIG.MD": f"{MANIFEST_BASE}/config_file.md",
}

logger = structlog.get_logger()

# Month encoding for 8.3 firmware filename: 1-9 for Jan-Sep, A-C for Oct-Dec
_MONTH_CHAR = {i: (str(i) if i <= 9 else chr(ord("A") + i - 10)) for i in range(1, 13)}
# Hour encoding: 0-9 for hours 0-9, A-N for hours 10-23
_HOUR_CHAR = {i: (str(i) if i <= 9 else chr(ord("A") + i - 10)) for i in range(24)}


# Camera variant letter used as the first character of the 8.3 filename when
# a variant is known, so the two images of a dual-image MANIFEST are
# distinguishable at a glance (R........IMG / H........IMG)
_VARIANT_LETTER = {"RP3": "R", "HM0360": "H"}


def firmware_83_filename(version: str, build_date: Optional[str] = None, variant: Optional[str] = None) -> str:
    """Convert a Himax firmware version string into an 8.3 filename.

    Without a variant: YYMDDHMM.IMG
        YY  = last 2 digits of year
        M   = month (1-9 for Jan-Sep, A for Oct, B for Nov, C for Dec)
        DD  = day of month (01-31)
        H   = hour (0-9 for hours 0-9, A-N for hours 10-23)
        MM  = minute (00-59)

    With a variant ('RP3' or 'HM0360'): VYMDDHMM.IMG - the variant letter
    (R/H) replaces the first year digit so both images of a dual-image
    MANIFEST have distinct, self-describing names.

    The version string from CI looks like:
        "WW500_C02 10:59:43 May 20 2026"

    Falls back to 'output.img' (or 'R_OUT.IMG'/'H_OUT.IMG') if the version
    cannot be parsed.
    """
    letter = _VARIANT_LETTER.get(variant or "")
    try:
        # Try to extract time and date from version string
        # Pattern: optional_board HH:MM:SS Mon DD YYYY
        m = re.search(
            r"(\d{2}):(\d{2}):\d{2}\s+(\w{3})\s+(\d{1,2})\s+(\d{4})",
            version,
        )
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2))
            month_abbr = m.group(3)
            day = int(m.group(4))
            year = int(m.group(5))

            month_num = list(calendar.month_abbr).index(month_abbr)
            if letter:
                y = year % 10
                return f"{letter}{y}{_MONTH_CHAR[month_num]}{day:02d}{_HOUR_CHAR[hour]}{minute:02d}.IMG"
            yy = year % 100
            return f"{yy:02d}{_MONTH_CHAR[month_num]}{day:02d}{_HOUR_CHAR[hour]}{minute:02d}.IMG"

        # Fallback: try parsing build_date (e.g. "May 20 2026") — no time info
        if build_date:
            dt = datetime.strptime(build_date, "%b %d %Y")
            if letter:
                return f"{letter}{dt.year % 10}{_MONTH_CHAR[dt.month]}{dt.day:02d}000.IMG"
            yy = dt.year % 100
            return f"{yy:02d}{_MONTH_CHAR[dt.month]}{dt.day:02d}000.IMG"
    except (ValueError, IndexError, KeyError):
        pass

    return f"{letter}_OUT.IMG" if letter else "output.img"


class ManifestDomainError(Exception):
    """Raised when manifest generation fails."""

    pass


# ── Helpers ──────────────────────────────────────────────────────────


def _flatten_directory(directory: Path) -> None:
    """Move all files from subdirectories into the root and remove subdirs.

    Ported from app.py flatten_directory().
    """
    for item in list(directory.rglob("*")):
        if item.is_file() and item.parent != directory:
            target = directory / item.name
            if target.exists():
                target.unlink()
            shutil.move(str(item), str(target))

    for item in directory.iterdir():
        if item.is_dir():
            shutil.rmtree(item)


def _extract_hex_array(c_content: str) -> bytes:
    """Parse a C byte array and return raw bytes.

    Pattern: const unsigned char array_name[] = { 0xNN, 0xNN, ... };
    """
    pattern = r"const\s+unsigned\s+char\s+\w+\[\]\s*=\s*\{([^}]+)\}"
    match = re.search(pattern, c_content, re.DOTALL)

    if not match:
        raise ManifestDomainError("Could not find byte array in C file")

    hex_values = re.findall(r"0x([0-9a-fA-F]{2})", match.group(1))
    if not hex_values:
        raise ManifestDomainError("No hex values found in C array")

    return bytes([int(h, 16) for h in hex_values])


# ── Config firmware fetching ─────────────────────────────────────────


async def _fetch_config_firmware(client, manifest_dir: Path) -> bool:
    """Fetch and extract the latest config firmware into manifest_dir.

    Tries DB record first, then falls back to storage bucket discovery.
    Returns True if config was successfully added.
    """
    # Try DB record
    try:
        response = await asyncio.to_thread(
            client.table("firmware")
            .select("*")
            .eq("type", "config")
            .eq("is_active", True)
            .is_("deleted_at", "null")
            .order("created_at", desc=True)
            .limit(1)
            .execute
        )

        if response.data:
            config_fw = rows_of(response)[0]
            path = config_fw["location_path"]
            content = await download_from_storage("firmware", path, silent=True)

            if content:
                if path.lower().endswith(".zip"):
                    # Extract ZIP contents into manifest dir

                    with zipfile.ZipFile(io.BytesIO(content)) as zf:
                        zf.extractall(manifest_dir)
                else:
                    filename = path.split("/")[-1]
                    (manifest_dir / filename).write_bytes(content)

                logger.info(
                    "config_firmware_added",
                    version=config_fw.get("version", "latest"),
                )
                return True
    except Exception as e:
        logger.warning("config_firmware_db_failed", error=str(e))

    # Fallback: list files in the firmware/config bucket folder
    try:
        files = await asyncio.to_thread(
            client.storage.from_("firmware").list,
            "config",
            {"sortBy": {"column": "created_at", "order": "desc"}},
        )
        if not files:
            files = await asyncio.to_thread(client.storage.from_("firmware").list, "config")
            files.sort(key=lambda x: x.get("created_at", x.get("name")), reverse=True)

        # Filter out placeholders
        files = [f for f in files if f["name"] != ".emptyFolderPlaceholder" and not f["name"].endswith("/")]

        if files:
            latest = files[0]["name"]
            content = await download_from_storage("firmware", f"config/{latest}", silent=True)
            if content:
                if latest.lower().endswith(".zip"):
                    with zipfile.ZipFile(io.BytesIO(content)) as zf:
                        zf.extractall(manifest_dir)
                else:
                    (manifest_dir / latest).write_bytes(content)
                logger.info("config_firmware_fallback", filename=latest)
                return True
    except Exception as e:
        logger.warning("config_firmware_discovery_failed", error=str(e))

    return False


# ── Himax firmware fetching ──────────────────────────────────────────


async def _fetch_himax_firmware(
    client,
    manifest_dir: Path,
    himax_firmware_id: Optional[str] = None,
    variant: Optional[str] = None,
    use_storage_fallback: bool = True,
) -> tuple[bool, str, Optional[str]]:
    """Fetch a Himax firmware image into manifest_dir.

    The firmware is stored in the `firmware` bucket under the `himax/` prefix.
    The CI pipeline (upload_firmware.yml) uploads it with type='himax'.

    The file is saved using the YYMDDHMM.IMG 8.3 filename derived from the
    firmware build timestamp, so the mobile app and device firmware can
    identify the build from its filename on the SD card.

    If himax_firmware_id is provided, fetches that specific record.
    Otherwise, fetches the latest active Himax firmware record - filtered to
    the given camera variant when one is specified.

    Returns (success, filename, variant) — filename is the 8.3 name used on disk,
    variant is the record's camera_variant (None on the storage-listing fallback).
    """
    default_name = "output.img"

    # Strategy 1: DB record
    try:
        query = client.table("firmware").select("*").eq("type", "himax").is_("deleted_at", "null")
        if himax_firmware_id:
            query = query.eq("id", himax_firmware_id)
        else:
            query = query.eq("is_active", True)
            if variant:
                query = query.eq("camera_variant", variant)

        response = await asyncio.to_thread(query.order("created_at", desc=True).limit(1).execute)

        if response.data:
            himax_fw = rows_of(response)[0]
            path = himax_fw["location_path"]
            content = await download_from_storage("firmware", path, silent=True)

            if content:
                img_name = firmware_83_filename(
                    himax_fw.get("version") or "",
                    himax_fw.get("build_date"),
                    himax_fw.get("camera_variant") or variant,
                )
                (manifest_dir / img_name).write_bytes(content)
                logger.info(
                    "himax_firmware_added",
                    version=himax_fw.get("version", "latest"),
                    filename=img_name,
                    size_bytes=len(content),
                )
                return True, img_name, himax_fw.get("camera_variant") or variant
    except Exception as e:
        logger.warning("himax_firmware_db_failed", error=str(e))

    # Strategy 2: Fallback — list files in the himax/ folder of the firmware bucket
    if not use_storage_fallback:
        return False, default_name, None
    try:
        files = await asyncio.to_thread(
            client.storage.from_("firmware").list,
            "himax",
            {"sortBy": {"column": "created_at", "order": "desc"}},
        )
        if not files:
            files = await asyncio.to_thread(client.storage.from_("firmware").list, "himax")
            files.sort(key=lambda x: x.get("created_at", x.get("name")), reverse=True)

        files = [f for f in files if f["name"] != ".emptyFolderPlaceholder" and not f["name"].endswith("/")]

        if files:
            latest = files[0]["name"]
            content = await download_from_storage("firmware", f"himax/{latest}", silent=True)
            if content:
                img_name = default_name
                if created_at_str := files[0].get("created_at"):
                    try:
                        dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                        img_name = firmware_83_filename("fallback", dt.strftime("%b %d %Y"))
                    except (ValueError, IndexError, KeyError):
                        pass
                (manifest_dir / img_name).write_bytes(content)
                logger.info("himax_firmware_fallback", filename=latest, saved_as=img_name)
                return True, img_name, None
    except Exception as e:
        logger.warning("himax_firmware_discovery_failed", error=str(e))

    return False, default_name, None


async def _fetch_himax_firmware_pair(
    client,
    manifest_dir: Path,
    himax_firmware_id: Optional[str] = None,
) -> tuple[bool, list[str]]:
    """Fetch the Himax firmware image PAIR (RP3 + HM0360) into manifest_dir.

    The WW500 holds two firmware images in A/B flash slots, so the MANIFEST
    should carry both camera variants. Selection rules:

    - himax_firmware_id given: fetch that record, then the latest active record
      of the OTHER variant (when the selected record has a variant).
    - Otherwise: latest active record of each variant.
    - Legacy databases with no variant-labelled records fall back to the old
      single-image behaviour (latest active, storage-listing fallback allowed).

    Returns (any_added, filenames).
    """
    filenames: list[str] = []
    fetched_variants: set[str] = set()

    # Explicitly selected record first (any variant, including legacy NULL)
    if himax_firmware_id:
        ok, name, var = await _fetch_himax_firmware(client, manifest_dir, himax_firmware_id, use_storage_fallback=False)
        if ok:
            filenames.append(name)
            # _fetch_himax_firmware already read the row, so it returns the variant
            # directly — no second query needed to complete the pair.
            if var:
                fetched_variants.add(var)

    # Latest active per remaining variant
    for variant in ("RP3", "HM0360"):
        if variant in fetched_variants:
            continue
        ok, name, _ = await _fetch_himax_firmware(client, manifest_dir, variant=variant, use_storage_fallback=False)
        if ok:
            filenames.append(name)
            fetched_variants.add(variant)

    # Legacy fallback: no variant-labelled records at all
    if not filenames:
        ok, name, _ = await _fetch_himax_firmware(client, manifest_dir)
        if ok:
            filenames.append(name)

    if len(filenames) == 1:
        logger.warning(
            "manifest_single_himax_variant",
            detail="only one camera variant available - dual-image switching needs both",
        )

    return bool(filenames), filenames


# ── AI model fetching ────────────────────────────────────────────────


async def _fetch_default_model(client, manifest_dir: Path) -> bool:
    """Fetch the default AI model into manifest_dir.

    Downloads the TFL and TXT files directly from Supabase Storage.
    Tries Person Detector first, then falls back to any available model.
    Returns True if a model was successfully added.
    """
    queries = [
        ("ilike", "name", "%Person%Detector%"),
        ("ilike", "name", "%Person%"),
        (None, None, None),  # fallback: latest any model
    ]

    for query_type, field, pattern in queries:
        try:
            # Only models whose conversion finished have storage paths; an
            # unconverted model carries NULL paths and must never be picked.
            q = client.table("ai_models").select("model_path, labels_path, name").is_("deleted_at", "null").not_.is_("model_path", "null")
            if query_type == "ilike":
                q = q.ilike(field, pattern)
            response = q.order("created_at", desc=True).limit(1).execute()

            if response.data:
                model = rows_of(response)[0]
                tfl_content = await download_from_storage("ai-models", model["model_path"], silent=True)
                if tfl_content:
                    tfl_filename = model["model_path"].split("/")[-1]
                    (manifest_dir / tfl_filename).write_bytes(tfl_content)

                    txt_content = await download_from_storage("ai-models", model["labels_path"], silent=True)
                    if txt_content:
                        txt_filename = model["labels_path"].split("/")[-1]
                        (manifest_dir / txt_filename).write_bytes(txt_content)

                    logger.info("ai_model_added", name=model.get("name", "default"))
                    return True
        except Exception as e:
            logger.debug("model_query_failed", pattern=pattern, error=str(e))
            continue

    return False


async def _fetch_github_model(model_type: str, resolution: str, manifest_dir: Path) -> bool:
    """Download and package a pre-trained model from GitHub into manifest_dir.

    Returns True on success.
    """
    try:
        config = get_model_config(model_type, resolution)
    except ValueError as e:
        logger.error("github_model_config_error", error=str(e))
        return False

    # From the config, not a second read of MODEL_REGISTRY: one lookup helper
    # means the two packaging paths cannot disagree about a model's labels the
    # way they did in #134.
    labels = config.get("labels") or []
    if not labels:
        logger.error("github_model_has_no_labels", model=model_type, resolution=resolution)
        return False

    try:
        content = await download_url_content(config["url"])

        # Convert if needed (cc_array → raw binary)
        if config["type"] == "cc_array":
            model_binary = _extract_hex_array(content.decode("utf-8"))
        else:
            model_binary = content

        if not model_binary:
            return False

        # Save as .TFL
        tfl_path = manifest_dir / "trained_vela.TFL"
        tfl_path.write_bytes(model_binary)

        # Save labels
        label_arcname = "trained_vela.TXT"
        (manifest_dir / label_arcname).write_text("\n".join(labels), newline="\n")

        logger.info("github_model_added", model=model_type, resolution=resolution)
        return True

    except (DownloadError, Exception) as e:
        logger.error("github_model_failed", error=str(e))
        return False


# ── GitHub-sourced firmware helpers ──────────────────────────────────


async def _fetch_github_manifest_files(
    branch: str,
    manifest_dir: Path,
) -> dict[str, bool]:
    """Download manifest files (e.g., CONFIG.TXT) from GitHub."""
    results: dict[str, bool] = {}
    for filename, gh_path in _GITHUB_MANIFEST_FILES.items():
        url = f"https://raw.githubusercontent.com/{GROVE_VISION_REPO}/{branch}/{gh_path}"
        try:
            content = await download_url_content(url)
            (manifest_dir / filename).write_bytes(content)
            results[filename] = True
            logger.info("github_file_downloaded", file=filename, branch=branch)
        except DownloadError as exc:
            results[filename] = False
            logger.warning("github_file_failed", file=filename, error=str(exc))
    return results


async def _resolve_project_model(client, project_id: str) -> dict:
    """Query project → ai_models → ai_model_families to resolve firmware IDs.

    Returns dict with keys: has_model, model_path, labels_path,
    firmware_model_id, version_number, model_name, model_version.
    """
    query = (
        client.table("projects")
        .select(
            "model_id, ai_models(id, name, version, model_path, labels_path, model_family_id, version_number, ai_model_families(firmware_model_id))"
        )
        .eq("id", project_id)
    )
    response = await asyncio.to_thread(query.execute)

    if not response.data:
        raise ManifestDomainError(f"Project {project_id} not found")

    project = rows_of(response)[0]
    if not project.get("model_id") or not project.get("ai_models"):
        return {"has_model": False}

    model = project["ai_models"]

    # A model still converting (or whose conversion failed) has NULL storage
    # paths — there's nothing to package, so treat it as "no model" rather than
    # trying to download a NULL path downstream.
    if not model.get("model_path") or not model.get("labels_path"):
        logger.info("project_model_not_ready", project_id=project_id, model=model.get("name"))
        return {"has_model": False}

    family = model.get("ai_model_families") or {}
    fw_model_id = family.get("firmware_model_id")
    version_number = model.get("version_number")

    if not fw_model_id or not version_number:
        raise ManifestDomainError(f"Model {model.get('name')} is missing firmware_model_id or version_number")

    return {
        "has_model": True,
        "model_path": model.get("model_path"),
        "labels_path": model.get("labels_path"),
        "firmware_model_id": fw_model_id,
        "version_number": version_number,
        "model_name": model.get("name"),
        "model_version": model.get("version"),
    }


async def _fetch_github_branches_raw() -> list[str]:
    """Inner fetch — hits the GitHub API directly. Call fetch_github_branches() instead."""
    url = f"https://api.github.com/repos/{GROVE_VISION_REPO}/branches"
    try:
        content = await download_url_content(url)
        data = json.loads(content)
        return [b["name"] for b in data]
    except Exception as exc:
        logger.warning("github_branches_failed", error=str(exc))
        return DEFAULT_FIRMWARE_BRANCHES


async def fetch_github_branches() -> list[str]:
    """Return firmware branch names, cached for 1 hour to avoid GitHub rate limits.

    The branch list changes at most a few times per month, so a 1-hour TTL is safe.
    All users share the same cache entry — no per-user data is involved.
    """
    return await cached(
        key="github:firmware_branches",
        ttl=3600,
        fetch_fn=_fetch_github_branches_raw,
    )


# ── Main entry point ─────────────────────────────────────────────────


async def generate_manifest(
    model_source: str = "default",
    model_type: Optional[str] = None,
    model_name: Optional[str] = None,
    model_id: Optional[int] = None,
    model_version: Optional[int] = None,
    resolution: Optional[str] = None,
    sscma_model_id: Optional[str] = None,
    org_model_id: Optional[str] = None,
    camera_type: str = "Grove Vision AI V2",
    project_id: Optional[str] = None,
    github_branch: str = "main",
    himax_firmware_id: Optional[str] = None,
    on_progress=None,
) -> bytes:
    """Generate a complete MANIFEST.zip package for SD card deployment.

    Args:
        model_source: 'My Project', 'Pre-trained Model', etc., or legacy 'github'.
        model_type: Legacy model name from MODEL_REGISTRY.
        model_name: New frontend model name.
        model_id: Target firmware OP14 ID.
        model_version: Target firmware OP15 version.
        resolution: e.g. '192x192'.
        sscma_model_id: For 'sscma' source — model catalog ID.
        org_model_id: For 'organisation' source — Supabase ai_models.id.
        camera_type: Camera config key from CAMERA_CONFIGS.
        project_id: For 'project' source — Supabase projects.id.
        github_branch: Branch for GitHub-sourced firmware files.
        himax_firmware_id: Supabase firmware.id for Himax firmware.
    """
    # Map frontend friendly names to backend legacy names
    if model_source == "My Project":
        model_source = "project"
    elif model_source == "Pre-trained Model":
        model_source = "github"
        if model_name:
            model_type = model_name.rsplit(" (", 1)[0]
    elif model_source == "SenseCap Models":
        model_source = "sscma"
    elif model_source == "My Organization Models":
        model_source = "organisation"
    elif model_source == "No Model":
        model_source = "none"

    client = create_service_client()

    async def _report(msg: str) -> None:
        if on_progress:
            await on_progress(msg)

    temp_dir = Path(tempfile.mkdtemp())
    manifest_dir = temp_dir / "MANIFEST"
    manifest_dir.mkdir()

    # Determine target filenames
    tfl_name = "trained_vela.TFL"
    txt_name = "trained_vela.TXT"
    if model_id is not None and model_version is not None:
        name_stem = f"{model_id}V{model_version}"
        if len(name_stem) > 8:
            name_stem = name_stem[:8]
        tfl_name = f"{name_stem}.TFL"
        txt_name = f"{name_stem}.TXT"

    try:
        # ── PROJECT SOURCE: GitHub firmware + Supabase model ──────
        if model_source == "project" and project_id:
            # 1. Download firmware files from GitHub
            await _report("Downloading firmware files from GitHub…")
            gh_results = await _fetch_github_manifest_files(github_branch, manifest_dir)
            config_added = gh_results.get("CONFIG.TXT", False)

            # 2. Download Himax firmware pair (both camera variants) from database
            await _report("Downloading Himax firmware from database…")
            himax_added, _ = await _fetch_himax_firmware_pair(client, manifest_dir, himax_firmware_id)

            # 3. Resolve project model
            await _report("Resolving project model…")
            model_info = await _resolve_project_model(client, project_id)
            model_added = False

            if model_info["has_model"]:
                fw_id = model_info["firmware_model_id"]
                ver = model_info["version_number"]
                stem = f"{fw_id}V{ver}"
                if len(stem) > 8:
                    stem = stem[:8]
                proj_tfl = f"{stem}.TFL"
                proj_txt = f"{stem}.TXT"

                # Download model binary
                await _report(f"Downloading model {proj_tfl}…")
                m_content = await download_from_storage("ai-models", model_info["model_path"])
                if m_content:
                    (manifest_dir / proj_tfl).write_bytes(m_content)
                    model_added = True

                    # Download labels
                    await _report(f"Downloading labels {proj_txt}…")
                    l_content = await download_from_storage("ai-models", model_info["labels_path"])
                    if l_content:
                        (manifest_dir / proj_txt).write_bytes(l_content)

                # Inject OP 14/15 into CONFIG.TXT
                await _report("Injecting model parameters into CONFIG.TXT…")
                config_path = manifest_dir / "CONFIG.TXT"
                if config_path.exists():
                    lines = config_path.read_text().splitlines()
                    lines = [ln for ln in lines if not (ln.strip().startswith("14 ") or ln.strip().startswith("15 "))]
                    lines.append(f"14 {fw_id}")
                    lines.append(f"15 {ver}")

                    def _sort_key(line: str):
                        s = line.strip()
                        if s.startswith("#"):
                            return (-1, 0)
                        parts = s.split()
                        if parts and parts[0].isdigit():
                            return (0, int(parts[0]))
                        return (1, 0)

                    lines.sort(key=_sort_key)
                    config_path.write_text("\n".join(lines) + "\n", newline="\n")

                logger.info(
                    "project_model_added",
                    model=model_info["model_name"],
                    tfl=proj_tfl,
                )
            else:
                logger.info("project_no_model", project_id=project_id)
                model_added = True  # Not an error — just no model files

        # ── LEGACY SOURCES ───────────────────────────────────────
        else:
            # 1. Fetch config firmware
            config_added = await _fetch_config_firmware(client, manifest_dir)
            if not config_added:
                logger.warning("manifest_no_config", camera=camera_type)
                cam_config = CAMERA_CONFIGS.get(camera_type, {})
                if cam_config.get("url"):
                    try:
                        content = await download_url_content(cam_config["url"])
                        (manifest_dir / cam_config["filename"]).write_bytes(content)
                        config_added = True
                    except DownloadError:
                        pass

            # 2. Fetch AI model based on source
            model_added = False

            if model_source == "github" and model_type and resolution:
                model_added = await _fetch_github_model(model_type, resolution, manifest_dir)
                if model_added and tfl_name != "trained_vela.TFL":
                    default_tfl = manifest_dir / "trained_vela.TFL"
                    default_txt = manifest_dir / "trained_vela.TXT"
                    if default_tfl.exists():
                        default_tfl.rename(manifest_dir / tfl_name)
                    if default_txt.exists():
                        default_txt.rename(manifest_dir / txt_name)

            elif model_source == "organisation" and org_model_id:
                try:
                    response = await asyncio.to_thread(
                        client.table("ai_models")
                        .select("model_path, labels_path, name, version, ai_model_families(firmware_model_id)")
                        .eq("id", org_model_id)
                        .execute
                    )
                    if response.data:
                        model = rows_of(response)[0]
                        family_data = model.get("ai_model_families")
                        family = (
                            family_data[0]
                            if isinstance(family_data, list) and family_data
                            else (family_data if isinstance(family_data, dict) else None)
                        )
                        firmware_id = family.get("firmware_model_id") if family else None
                        if not firmware_id:
                            raise ManifestDomainError(f"Model family for {org_model_id} is missing a firmware_model_id")
                        # The user explicitly chose this model — if it hasn't
                        # finished converting (NULL paths), say so clearly.
                        if not model.get("model_path") or not model.get("labels_path"):
                            raise ManifestDomainError(
                                f"Model '{model.get('name')}' is still converting (or its conversion failed); it has no stored files yet."
                            )
                        version_str = model.get("version", "1")
                        version = version_str.split(".")[0] if "." in version_str else version_str

                        name_stem = f"{firmware_id}V{version}"
                        if len(name_stem) > 8:
                            name_stem = name_stem[:8]

                        dyn_tfl = f"{name_stem}.TFL"
                        dyn_txt = f"{name_stem}.TXT"

                        # Download model binary
                        m_content = await download_from_storage("ai-models", model["model_path"])
                        if m_content:
                            (manifest_dir / dyn_tfl).write_bytes(m_content)
                            model_added = True

                        # Download labels
                        l_content = await download_from_storage("ai-models", model["labels_path"])
                        if l_content:
                            (manifest_dir / dyn_txt).write_bytes(l_content)

                            logger.info(
                                "org_model_added",
                                name=model.get("name"),
                                tfl_name=dyn_tfl,
                            )
                except Exception as e:
                    logger.error("org_model_failed", error=str(e))
            elif model_source == "sscma" and sscma_model_id:
                logger.warning("sscma_model_not_yet_implemented")
            elif model_source == "none":
                logger.info("skip_ai_model")
                model_added = True
            else:
                model_added = await _fetch_default_model(client, manifest_dir)

            # 3. Fetch Himax firmware image pair (both camera variants) from database
            himax_added, _ = await _fetch_himax_firmware_pair(client, manifest_dir)
            if not himax_added:
                logger.warning("manifest_no_himax_firmware")

        # 4. Flatten nested directories
        _flatten_directory(manifest_dir)

        # 5. Create final MANIFEST.zip (uncompressed for SD card)
        await _report("Zipping MANIFEST folder…")
        files_to_zip = list(manifest_dir.glob("*"))
        if not files_to_zip:
            raise ManifestDomainError("No files found for MANIFEST — all downloads failed")

        final_zip_path = temp_dir / "MANIFEST_final.zip"
        with zipfile.ZipFile(final_zip_path, "w", zipfile.ZIP_STORED) as zipf:
            for file in files_to_zip:
                if file.is_file():
                    zipf.write(file, f"MANIFEST/{file.name}")

        manifest_bytes = final_zip_path.read_bytes()
        logger.info(
            "manifest_generated",
            size_bytes=len(manifest_bytes),
            files=len(files_to_zip),
            config=config_added,
            model=model_added,
            himax=himax_added,
        )

        return manifest_bytes

    except ManifestDomainError:
        raise
    except Exception as e:
        raise ManifestDomainError(f"Failed to generate MANIFEST: {e}") from e
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
