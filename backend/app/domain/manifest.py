# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Manifest generation domain — ported from app.py L721-852.

Orchestrates: fetch config firmware → fetch AI model → assemble MANIFEST.zip.
Reusable by both the API handler and the async ARQ worker.

The MANIFEST.zip is what gets deployed to the camera SD card. Structure:
    MANIFEST/
    ├── CONFIG.TXT          # Camera configuration
    ├── trained_vela.TFL    # AI model binary
    └── trained_vela.TXT    # Model labels
"""

import re
import json
import shutil
import zipfile
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

import structlog

from app.config import settings
from app.registries.model_registry import MODEL_REGISTRY, get_model_config
from app.registries.camera_configs import CAMERA_CONFIGS
from app.services.supabase_client import create_service_client
from app.services.storage import download_from_storage
from app.services.http_client import download_url_content, DownloadError

logger = structlog.get_logger()


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
        response = (
            client.table("firmware")
            .select("*")
            .eq("type", "config")
            .eq("is_active", True)
            .is_("deleted_at", "null")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if response.data:
            config_fw = response.data[0]
            path = config_fw["location_path"]
            content = await download_from_storage("firmware", path, silent=True)

            if content:
                if path.lower().endswith(".zip"):
                    # Extract ZIP contents into manifest dir
                    import io
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
        files = client.storage.from_("firmware").list(
            "config", {"sortBy": {"column": "created_at", "order": "desc"}}
        )
        if not files:
            files = client.storage.from_("firmware").list("config")
            files.sort(
                key=lambda x: x.get("created_at", x.get("name")), reverse=True
            )

        # Filter out placeholders
        files = [
            f
            for f in files
            if f["name"] != ".emptyFolderPlaceholder" and not f["name"].endswith("/")
        ]

        if files:
            latest = files[0]["name"]
            content = await download_from_storage(
                "firmware", f"config/{latest}", silent=True
            )
            if content:
                if latest.lower().endswith(".zip"):
                    import io
                    with zipfile.ZipFile(io.BytesIO(content)) as zf:
                        zf.extractall(manifest_dir)
                else:
                    (manifest_dir / latest).write_bytes(content)
                logger.info("config_firmware_fallback", filename=latest)
                return True
    except Exception as e:
        logger.warning("config_firmware_discovery_failed", error=str(e))

    return False


# ── AI model fetching ────────────────────────────────────────────────

async def _fetch_default_model(client, manifest_dir: Path) -> bool:
    """Fetch and extract the default AI model into manifest_dir.

    Tries Person Detector first, then falls back to any available model.
    Returns True if a model was successfully added.
    """
    # Try to find models in priority order
    queries = [
        ("ilike", "name", "%Person%Detector%"),
        ("ilike", "name", "%Person%"),
        (None, None, None),  # fallback: latest any model
    ]

    for query_type, field, pattern in queries:
        try:
            q = client.table("ai_models").select("*").is_("deleted_at", "null")
            if query_type == "ilike":
                q = q.ilike(field, pattern)
            response = q.order("created_at", desc=True).limit(1).execute()

            if response.data:
                model = response.data[0]
                path = model["storage_path"]
                content = await download_from_storage("ai-models", path, silent=True)

                if content:
                    import io
                    with zipfile.ZipFile(io.BytesIO(content)) as zf:
                        zf.extractall(manifest_dir)
                    logger.info("ai_model_added", name=model.get("name", "default"))
                    return True
        except Exception as e:
            logger.debug("model_query_failed", pattern=pattern, error=str(e))
            continue

    # Last resort: discover from storage bucket
    try:
        org_folder = settings.GENERAL_ORG_ID
        subdirs = client.storage.from_("ai-models").list(org_folder, {"limit": 5})
        if subdirs:
            for sd in subdirs:
                model_name = sd["name"]
                files = client.storage.from_("ai-models").list(
                    f"{org_folder}/{model_name}"
                )
                for f in files:
                    if f["name"] == "ai_model.zip":
                        content = await download_from_storage(
                            "ai-models",
                            f"{org_folder}/{model_name}/{f['name']}",
                            silent=True,
                        )
                        if content:
                            import io
                            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                                zf.extractall(manifest_dir)
                            logger.info("ai_model_fallback", name=model_name)
                            return True
    except Exception as e:
        logger.warning("ai_model_discovery_failed", error=str(e))

    return False


async def _fetch_github_model(
    model_type: str, resolution: str, manifest_dir: Path
) -> bool:
    """Download and package a pre-trained model from GitHub into manifest_dir.

    Returns True on success.
    """
    try:
        config = get_model_config(model_type, resolution)
    except ValueError as e:
        logger.error("github_model_config_error", error=str(e))
        return False

    labels = MODEL_REGISTRY[model_type].get("labels", ["unknown"])

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
        (manifest_dir / label_arcname).write_text("\n".join(labels))

        logger.info("github_model_added", model=model_type, resolution=resolution)
        return True

    except (DownloadError, Exception) as e:
        logger.error("github_model_failed", error=str(e))
        return False


# ── Main entry point ─────────────────────────────────────────────────

async def generate_manifest(
    model_source: str = "default",
    model_type: Optional[str] = None,
    resolution: Optional[str] = None,
    sscma_model_id: Optional[str] = None,
    org_model_id: Optional[str] = None,
    camera_type: str = "Raspberry Pi",
) -> bytes:
    """Generate a complete MANIFEST.zip package for SD card deployment.

    Args:
        model_source: One of 'default', 'github', 'sscma', 'organisation'.
        model_type: For 'github' source — model name from MODEL_REGISTRY.
        resolution: For 'github' source — e.g. '192x192'.
        sscma_model_id: For 'sscma' source — model catalog ID.
        org_model_id: For 'organisation' source — Supabase ai_models.id.
        camera_type: Camera config key from CAMERA_CONFIGS.

    Returns:
        Bytes of the final MANIFEST.zip.

    Raises:
        ManifestDomainError: If generation fails.
    """
    client = create_service_client()

    temp_dir = Path(tempfile.mkdtemp())
    manifest_dir = temp_dir / "MANIFEST"
    manifest_dir.mkdir()

    try:
        # 1. Fetch config firmware
        config_added = await _fetch_config_firmware(client, manifest_dir)
        if not config_added:
            logger.warning("manifest_no_config", camera=camera_type)
            # Non-fatal: camera config from static URL as fallback
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
        elif model_source == "organisation" and org_model_id:
            # Fetch specific org model by ID
            try:
                response = (
                    client.table("ai_models")
                    .select("storage_path, name")
                    .eq("id", org_model_id)
                    .execute()
                )
                if response.data:
                    model = response.data[0]
                    content = await download_from_storage(
                        "ai-models", model["storage_path"]
                    )
                    if content:
                        import io
                        with zipfile.ZipFile(io.BytesIO(content)) as zf:
                            zf.extractall(manifest_dir)
                        model_added = True
                        logger.info("org_model_added", name=model.get("name"))
            except Exception as e:
                logger.error("org_model_failed", error=str(e))
        elif model_source == "sscma" and sscma_model_id:
            # SSCMA model processing would go here
            # TODO: Port SSCMA model fetching + optional Vela conversion
            logger.warning("sscma_model_not_yet_implemented")
        else:
            # Default: fetch best available from DB
            model_added = await _fetch_default_model(client, manifest_dir)

        # 3. Flatten nested directories
        _flatten_directory(manifest_dir)

        # 4. Create final MANIFEST.zip (uncompressed for SD card)
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
        )

        return manifest_bytes

    except ManifestDomainError:
        raise
    except Exception as e:
        raise ManifestDomainError(f"Failed to generate MANIFEST: {e}") from e
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
