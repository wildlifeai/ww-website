# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Manifest generation domain — ported from app.py L721-852.

Orchestrates: fetch config firmware → fetch AI model → fetch Himax firmware → assemble MANIFEST.zip.
Reusable by both the API handler and the async ARQ worker.

The MANIFEST.zip is what gets deployed to the camera SD card. Structure:
    MANIFEST/
    ├── CONFIG.TXT          # Camera configuration
    ├── trained_vela.TFL    # AI model binary
    ├── trained_vela.TXT    # Model labels
    └── output.img          # Himax coprocessor firmware
"""

# ── GitHub repo constants ────────────────────────────────────────────
import asyncio
import io
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

import structlog

from app.config import settings
from app.registries.camera_configs import CAMERA_CONFIGS
from app.registries.model_registry import MODEL_REGISTRY, get_model_config
from app.services.http_client import DownloadError, download_url_content
from app.services.storage import download_from_storage
from app.services.supabase_client import create_service_client

GROVE_VISION_REPO = "wildlifeai/Seeed_Grove_Vision_AI_Module_V2"
MANIFEST_BASE = "EPII_CM55M_APP_S/app/ww_projects/ww500_md/MANIFEST"
OUTPUT_IMG_PATH = "we2_image_gen_local_dpd/output_case1_sec_wlcsp/output.img"

_GITHUB_MANIFEST_FILES = {
    "CONFIG.TXT": f"{MANIFEST_BASE}/CONFIG.TXT",
}

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
        files = client.storage.from_("firmware").list("config", {"sortBy": {"column": "created_at", "order": "desc"}})
        if not files:
            files = client.storage.from_("firmware").list("config")
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


async def _fetch_himax_firmware(client, manifest_dir: Path) -> bool:
    """Fetch the latest active Himax firmware image into manifest_dir.

    The firmware is stored as `output.img` in the `firmware` bucket under the
    `himax/` prefix.  The CI pipeline (build_and_release.yml) uploads it with
    type='himax'.

    Tries DB record first, then falls back to storage bucket discovery.
    Returns True if the firmware was successfully added.
    """
    # Strategy 1: DB record
    try:
        response = (
            client.table("firmware")
            .select("*")
            .eq("type", "himax")
            .eq("is_active", True)
            .is_("deleted_at", "null")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if response.data:
            himax_fw = response.data[0]
            path = himax_fw["location_path"]
            content = await download_from_storage("firmware", path, silent=True)

            if content:
                # Always save as output.img regardless of the versioned name in storage
                (manifest_dir / "output.img").write_bytes(content)
                logger.info(
                    "himax_firmware_added",
                    version=himax_fw.get("version", "latest"),
                    size_bytes=len(content),
                )
                return True
    except Exception as e:
        logger.warning("himax_firmware_db_failed", error=str(e))

    # Strategy 2: Fallback — list files in the himax/ folder of the firmware bucket
    try:
        files = client.storage.from_("firmware").list("himax", {"sortBy": {"column": "created_at", "order": "desc"}})
        if not files:
            files = client.storage.from_("firmware").list("himax")
            files.sort(key=lambda x: x.get("created_at", x.get("name")), reverse=True)

        files = [f for f in files if f["name"] != ".emptyFolderPlaceholder" and not f["name"].endswith("/")]

        if files:
            latest = files[0]["name"]
            content = await download_from_storage("firmware", f"himax/{latest}", silent=True)
            if content:
                (manifest_dir / "output.img").write_bytes(content)
                logger.info("himax_firmware_fallback", filename=latest)
                return True
    except Exception as e:
        logger.warning("himax_firmware_discovery_failed", error=str(e))

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
                files = client.storage.from_("ai-models").list(f"{org_folder}/{model_name}")
                for f in files:
                    if f["name"] == "ai_model.zip":
                        content = await download_from_storage(
                            "ai-models",
                            f"{org_folder}/{model_name}/{f['name']}",
                            silent=True,
                        )
                        if content:
                            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                                zf.extractall(manifest_dir)
                            logger.info("ai_model_fallback", name=model_name)
                            return True
    except Exception as e:
        logger.warning("ai_model_discovery_failed", error=str(e))

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


async def _fetch_github_output_img(branch: str, manifest_dir: Path) -> bool:
    """Download output.img from the Grove Vision AI repo."""
    url = f"https://raw.githubusercontent.com/{GROVE_VISION_REPO}/{branch}/{OUTPUT_IMG_PATH}"
    try:
        content = await download_url_content(url)
        (manifest_dir / "output.img").write_bytes(content)
        logger.info("github_output_img_downloaded", branch=branch, size=len(content))
        return True
    except DownloadError as exc:
        logger.warning("github_output_img_failed", error=str(exc))
        return False


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

    project = response.data[0]
    if not project.get("model_id") or not project.get("ai_models"):
        return {"has_model": False}

    model = project["ai_models"]
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


async def fetch_github_branches() -> list[str]:
    """Fetch branch names from the Grove Vision AI repo (public API)."""
    url = f"https://api.github.com/repos/{GROVE_VISION_REPO}/branches"
    try:
        content = await download_url_content(url)
        data = json.loads(content)
        return [b["name"] for b in data]
    except Exception as exc:
        logger.warning("github_branches_failed", error=str(exc))
        return ["main"]


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

            # 2. Download output.img from GitHub
            await _report("Downloading output.img…")
            himax_added = await _fetch_github_output_img(github_branch, manifest_dir)

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
                    config_path.write_text("\n".join(lines) + "\n")

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
                    response = (
                        client.table("ai_models")
                        .select("storage_path, name, version, ai_model_families(firmware_model_id)")
                        .eq("id", org_model_id)
                        .execute()
                    )
                    if response.data:
                        model = response.data[0]
                        content = await download_from_storage("ai-models", model["storage_path"])
                        if content:
                            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                                zf.extractall(manifest_dir)
                            model_added = True

                            family = model.get("ai_model_families")
                            firmware_id = family.get("firmware_model_id") if family else None
                            if not firmware_id:
                                raise ManifestDomainError(f"Model family for {org_model_id} is missing a firmware_model_id")
                            version_str = model.get("version", "1")
                            version = version_str.split(".")[0] if "." in version_str else version_str

                            name_stem = f"{firmware_id}V{version}"
                            if len(name_stem) > 8:
                                name_stem = name_stem[:8]

                            dyn_tfl = f"{name_stem}.TFL"
                            dyn_txt = f"{name_stem}.TXT"

                            for f in manifest_dir.iterdir():
                                if f.is_file() and f.suffix.upper() == ".TFL":
                                    f.rename(manifest_dir / dyn_tfl)
                                    break
                            for f in manifest_dir.iterdir():
                                if f.is_file() and f.suffix.upper() == ".TXT":
                                    f.rename(manifest_dir / dyn_txt)
                                    break

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

            # 3. Fetch Himax firmware image (output.img)
            himax_added = await _fetch_himax_firmware(client, manifest_dir)
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
