# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Vela model compiler subprocess wrapper.

Wraps the ethos-u-vela CLI for converting TFLite models to Ethos-U55 format.
"""

import subprocess
from pathlib import Path

import structlog

logger = structlog.get_logger()


class VelaConversionError(Exception):
    """Raised when Vela conversion fails."""

    pass


async def run_vela_conversion(
    input_path: Path,
    output_dir: Path,
    accelerator_config: str = "ethos-u55-64",
    memory_mode: str = "Shared_Sram",
    timeout: int = 120,
) -> Path:
    """Run Vela conversion on a TFLite model.

    Args:
        input_path: Path to the source .tflite file.
        output_dir: Directory for Vela output.
        accelerator_config: Target accelerator config.
        memory_mode: Memory mode for the target.
        timeout: Maximum seconds to wait for Vela.

    Returns:
        Path to the converted output file.

    Raises:
        VelaConversionError: If conversion fails.
    """
    cmd = [
        "vela",
        "--accelerator-config", accelerator_config,
        "--memory-mode", memory_mode,
        "--output-dir", str(output_dir),
        str(input_path),
    ]

    logger.info("vela_conversion_start", input=str(input_path))

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=timeout
        )
        logger.info("vela_conversion_success", stdout=result.stdout[:500])
    except subprocess.CalledProcessError as e:
        raise VelaConversionError(
            f"Vela failed (code {e.returncode}): {e.stderr[:500]}"
        ) from e
    except FileNotFoundError:
        raise VelaConversionError(
            "Vela command not found. Ensure ethos-u-vela is installed."
        )
    except subprocess.TimeoutExpired:
        raise VelaConversionError(f"Vela conversion timed out after {timeout}s")

    # Find output file
    return _find_vela_output(output_dir, input_path.name)


def _find_vela_output(work_dir: Path, original_name: str) -> Path:
    """Locate the Vela output file (same logic as app.py's find_vela_output)."""
    stem = Path(original_name).stem

    candidates = [
        work_dir / f"{stem}_vela.tflite",
        work_dir / "MOD00001.tfl",
        work_dir / "output.tflite",
    ]

    for path in candidates:
        if path.exists():
            return path

    # Fallback: original file may have been overwritten in-place
    original_path = work_dir / original_name
    if original_path.exists():
        return original_path

    raise VelaConversionError(
        f"Could not find Vela output in {work_dir}. Checked: {candidates}"
    )
