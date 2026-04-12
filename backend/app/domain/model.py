# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Model conversion domain — ported from app.py L430-574, L1007-1171.

Orchestrates: validate ZIP → extract tflite + labels → convert via Vela →
package ai_model.zip → upload to Supabase Storage → register in DB.

Reusable by both the API handler (sync for small ops) and the ARQ worker.
"""

import os
import re
import shutil
import zipfile
import tempfile
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

import structlog

from app.services.vela import run_vela_conversion, VelaConversionError
from app.services.supabase_client import create_service_client

logger = structlog.get_logger()


class ModelDomainError(Exception):
    """Raised when model processing fails."""

    pass


# ── Helpers (ported from app.py) ─────────────────────────────────────

def _parse_model_zip_name(zip_path: str) -> Tuple[str, str]:
    """Parse '<modelname>-custom-<version>.zip' → (modelname, version)."""
    name = os.path.basename(zip_path)
    if not name.endswith(".zip"):
        raise ValueError("Zip file must end with .zip")
    base = name[:-4]
    if "-custom-" in base:
        modelname, version = base.split("-custom-", 1)
        if modelname and version:
            return modelname, version
    return "unknown", "1.0.0"


def _safe_move(src: Path, dst: Path) -> None:
    """Safely move a file, creating parent dirs and overwriting old file."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    shutil.move(str(src), str(dst))


def _extract_labels_from_header(vars_h_path: Path) -> List[str]:
    """Extract classification labels from model_variables.h."""
    with open(vars_h_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    match = re.search(
        r'const char\*\s*ei_classifier_inferencing_categories.*?=\s*\{(.*?)\};',
        content,
        re.DOTALL,
    )
    if match:
        labels = re.findall(r'"([^"]+)"', match.group(1))
        if labels:
            return labels

    raise ModelDomainError("No labels found in model_variables.h")


def _build_firmware_filename(vars_h_path: Path) -> str:
    """Build 8.3-style filename from model_variables.h project/version IDs.

    Format: <project_id>V<deploy_version>.tfl (e.g. 1V1.TFL)
    """
    try:
        with open(vars_h_path, "r", encoding="utf-8", errors="replace") as f:
            header = f.read()
        pid_m = re.search(r"\.project_id\s*=\s*(\d+)", header)
        ver_m = re.search(r"\.deploy_version\s*=\s*(\d+)", header)
        if pid_m and ver_m:
            pid = str(int(pid_m.group(1)))
            ver = str(int(ver_m.group(1)))
            base = f"{pid}V{ver}"
            if len(base) > 8:
                logger.warning("filename_truncated", original=base)
                base = base[:8]
            return base + ".tfl"
    except Exception:
        pass

    return "MOD00001.tfl"


# ── Core domain operations ───────────────────────────────────────────


async def convert_uploaded_model(zip_content: bytes, filename: str) -> Tuple[bytes, List[str]]:
    """Convert an uploaded Edge Impulse ZIP through Vela.

    Args:
        zip_content: Raw bytes of the uploaded ZIP file.
        filename: Original filename (used for name/version extraction).

    Returns:
        Tuple of (ai_model_zip_bytes, labels_list).

    Raises:
        ModelDomainError: If any step fails.
    """
    model_name, model_version = _parse_model_zip_name(filename)
    container_name = f"{model_name}-custom-{model_version}"

    with tempfile.TemporaryDirectory() as temp_dir:
        base_path = Path(temp_dir)

        # 1. Save and extract uploaded ZIP
        uploaded_zip_path = base_path / filename
        uploaded_zip_path.write_bytes(zip_content)

        work_dir = base_path / "work" / container_name
        work_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(uploaded_zip_path, "r") as z:
            z.extractall(work_dir)

        tflite_path = work_dir / "trained.tflite"
        vars_h_path = work_dir / "model-parameters" / "model_variables.h"

        if not tflite_path.exists():
            raise ModelDomainError(f"trained.tflite not found in ZIP")
        if not vars_h_path.exists():
            raise ModelDomainError(f"model_variables.h not found in ZIP")

        logger.info("model_extracted", model=container_name)

        # 2. Run Vela conversion
        try:
            vela_output = await run_vela_conversion(tflite_path, work_dir)
        except VelaConversionError as e:
            raise ModelDomainError(str(e)) from e

        # 3. Rename to 8.3 firmware filename
        target_name = _build_firmware_filename(vars_h_path)
        vela_final_path = work_dir / target_name
        if vela_final_path.exists():
            vela_final_path.unlink()
        _safe_move(vela_output, vela_final_path)

        logger.info("vela_renamed", filename=target_name)

        # 4. Extract labels
        labels = _extract_labels_from_header(vars_h_path)

        labels_txt_path = work_dir / "labels.txt"
        labels_txt_path.write_text("\n".join(labels))

        # 5. Package ai_model.zip (uncompressed, firmware-compatible)
        ai_model_zip_path = work_dir / "ai_model.zip"
        model_arcname = vela_final_path.stem + ".TFL"
        label_arcname = vela_final_path.stem + ".TXT"

        with zipfile.ZipFile(ai_model_zip_path, "w", zipfile.ZIP_STORED) as zf:
            zf.write(vela_final_path, model_arcname)
            zf.write(labels_txt_path, label_arcname)

        if not ai_model_zip_path.exists():
            raise ModelDomainError("Failed to create ai_model.zip")

        result_bytes = ai_model_zip_path.read_bytes()
        logger.info("model_packaged", size_bytes=len(result_bytes), labels=labels)

        return result_bytes, labels


async def upload_and_register(
    model_bytes: bytes,
    model_name: str,
    model_version: str,
    description: str,
    labels: List[str],
    org_id: str,
    user_id: str,
) -> Dict[str, Any]:
    """Upload ai_model.zip to Supabase Storage and register in DB.

    Args:
        model_bytes: The ai_model.zip content.
        model_name: Display name for the model.
        model_version: Semantic version string.
        description: Model description.
        labels: Classification labels.
        org_id: Target organisation UUID.
        user_id: Uploading user's UUID.

    Returns:
        The created/updated model record from Supabase.

    Raises:
        ModelDomainError: If upload or registration fails.
    """
    client = create_service_client()

    # Sanitize to prevent path traversal
    safe_name = os.path.basename(model_name)
    safe_version = os.path.basename(model_version)
    storage_path = f"{org_id}/{safe_name}-custom-{safe_version}/ai_model.zip"

    # 1. Upload to storage
    try:
        client.storage.from_("ai-models").upload(
            path=storage_path,
            file=model_bytes,
            file_options={"content-type": "application/zip", "upsert": "true"},
        )
        logger.info("model_uploaded", path=storage_path)
    except Exception as e:
        raise ModelDomainError(f"Storage upload failed: {e}") from e

    # 2. Register in database (upsert by org_id + name + version)
    try:
        existing = (
            client.table("ai_models")
            .select("id")
            .eq("organisation_id", org_id)
            .eq("name", model_name)
            .eq("version", model_version)
            .is_("deleted_at", "null")
            .execute()
        )

        model_data = {
            "name": model_name,
            "version": model_version,
            "description": description,
            "organisation_id": org_id,
            "uploaded_by": user_id,
            "modified_by": user_id,
            "storage_path": storage_path,
            "file_size_bytes": len(model_bytes),
            "file_type": "manifest",
            "detection_capabilities": labels,
        }

        if existing.data:
            model_id = existing.data[0]["id"]
            response = (
                client.table("ai_models").update(model_data).eq("id", model_id).execute()
            )
            logger.info("model_updated", model_id=model_id)
        else:
            response = client.table("ai_models").insert(model_data).execute()
            logger.info("model_inserted")

        if not response.data:
            raise ModelDomainError("Database operation returned no data")

        return response.data[0]

    except ModelDomainError:
        raise
    except Exception as e:
        # Rollback: delete uploaded file from storage
        try:
            client.storage.from_("ai-models").remove([storage_path])
            logger.warning("model_storage_rollback", path=storage_path)
        except Exception as rollback_e:
            logger.error(
                "model_rollback_failed",
                path=storage_path,
                error=str(rollback_e),
            )
        raise ModelDomainError(f"Database registration failed: {e}") from e
