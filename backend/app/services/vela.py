# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Vela model compiler subprocess wrapper.

Wraps the ethos-u-vela CLI for converting TFLite models to Ethos-U55 format.
"""

import csv
import io
import re
import subprocess
from pathlib import Path
from typing import Optional

import structlog

from app.config import settings

logger = structlog.get_logger()


class VelaConversionError(Exception):
    """Raised when Vela conversion fails."""

    pass


class ArenaBudgetExceeded(VelaConversionError):
    """The compiled model needs more SRAM than the camera's tensor arena."""


def parse_sram_used_bytes(summary_csv: Optional[str], stdout: str = "") -> Optional[int]:
    """Peak SRAM Vela reports, in bytes, or None when it reports none.

    Vela writes ``<stem>_summary_<system>.csv`` with a ``sram_memory_used`` column
    in KiB and prints ``Total SRAM used ... KiB``; the CSV is read first.
    """
    rows = list(csv.DictReader(io.StringIO(summary_csv or "")))
    value = (rows[0].get("sram_memory_used") or "").strip() if rows else ""
    if not value:
        m = re.search(r"Total SRAM used\s+([0-9.]+)\s*KiB", stdout or "")
        value = m.group(1) if m else ""
    try:
        return int(round(float(value) * 1024)) if value else None
    except ValueError:
        return None


def check_arena_budget(sram_used_bytes: Optional[int], arena_bytes: int) -> None:
    """Refuse a model whose SRAM estimate exceeds the arena; an unknown estimate only logs."""
    if sram_used_bytes is None:
        logger.warning("vela_sram_unknown", arena_bytes=arena_bytes)
        return
    if sram_used_bytes > arena_bytes:
        raise ArenaBudgetExceeded(
            f"The compiled model needs {sram_used_bytes / 1024:.1f} KiB of SRAM but the camera's tensor arena is "
            f"{arena_bytes / 1024:.0f} KiB (MODEL_ARENA_BYTES). It would fail to load on the WW500: use a 96 px input, "
            "fewer classes or a smaller model."
        )


def compiled_sram_bytes(model_path: Path) -> Optional[int]:
    """SRAM an already Vela-compiled model needs: its ``_scratch`` tensor size, or None.

    Vela sizes that tensor from the figure its summary reports as
    ``sram_memory_used`` (``ethosu/vela/npu_serialisation.py``). ``_scratch_fast``
    aliases the same SRAM in Shared_Sram mode, so it is not added. None when the
    file has no scratch tensor (not Vela output) or will not parse.
    """
    from ethosu.vela.tflite.Model import Model

    try:
        subgraph = Model.GetRootAs(bytearray(Path(model_path).read_bytes()), 0).Subgraphs(0)
        if subgraph is None:
            raise ValueError("no subgraph")
        for i in range(subgraph.TensorsLength()):
            tensor = subgraph.Tensors(i)
            if tensor is None:
                raise ValueError(f"tensor {i} is missing")
            name = (tensor.Name() or b"").decode(errors="replace")
            if name.endswith("scratch") and tensor.ShapeLength() == 1:
                return int(tensor.Shape(0))
    except Exception:  # noqa: BLE001 unreadable means unknown, which only logs
        logger.info("compiled_sram_unreadable", file=Path(model_path).name)
    return None


def check_compiled_model(model_path: Path, arena_bytes: Optional[int] = None) -> None:
    """The arena check for a model that arrives compiled and so never meets Vela here."""
    budget = settings.MODEL_ARENA_BYTES if arena_bytes is None else arena_bytes
    sram = compiled_sram_bytes(model_path)
    logger.info("compiled_arena_check", file=Path(model_path).name, sram_used_bytes=sram, arena_bytes=budget)
    check_arena_budget(sram, budget)


async def run_vela_conversion(
    input_path: Path,
    output_dir: Path,
    accelerator_config: str = "ethos-u55-64",
    memory_mode: str = "Shared_Sram",
    timeout: int = 120,
    arena_bytes: Optional[int] = None,
) -> Path:
    """Run Vela conversion on a TFLite model, then check it fits the tensor arena.

    Args:
        input_path: Path to the source .tflite file.
        output_dir: Directory for Vela output.
        accelerator_config: Target accelerator config.
        memory_mode: Memory mode for the target.
        timeout: Maximum seconds to wait for Vela.
        arena_bytes: SRAM budget; defaults to ``settings.MODEL_ARENA_BYTES``.

    Returns:
        Path to the converted output file.

    Raises:
        VelaConversionError: If conversion fails.
        ArenaBudgetExceeded: If Vela's SRAM estimate exceeds the arena.
    """
    cmd = [
        "vela",
        "--accelerator-config",
        accelerator_config,
        "--memory-mode",
        memory_mode,
        "--output-dir",
        str(output_dir),
        str(input_path),
    ]

    logger.info("vela_conversion_start", input=str(input_path))

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout)
        logger.info("vela_conversion_success", stdout=result.stdout[:500])
    except subprocess.CalledProcessError as e:
        # The real reason (unsupported operator / not int8-quantized) is at the END
        # of Vela's output, after the Python traceback header — so surface the tail,
        # not the first 500 chars, and add an actionable hint.
        detail = ((e.stderr or "") + "\n" + (e.stdout or "")).strip()
        logger.error("vela_conversion_failed", returncode=e.returncode, detail=detail[-2000:])
        raise VelaConversionError(
            f"Vela failed (code {e.returncode}). The model must be a fully int8-quantized "
            "TFLite model compatible with the Ethos-U NPU — the usual causes are a float or "
            "only partially-quantized model, or an unsupported operator.\n\n"
            f"Vela output:\n{detail[-1200:]}"
        ) from e
    except FileNotFoundError:
        raise VelaConversionError("Vela command not found. Ensure ethos-u-vela is installed.")
    except subprocess.TimeoutExpired:
        raise VelaConversionError(f"Vela conversion timed out after {timeout}s")

    summary = next(iter(sorted(Path(output_dir).glob(f"{input_path.stem}_summary_*.csv"))), None)
    sram = parse_sram_used_bytes(summary.read_text(encoding="utf-8", errors="replace") if summary else None, result.stdout)
    budget = settings.MODEL_ARENA_BYTES if arena_bytes is None else arena_bytes
    logger.info("vela_arena_check", sram_used_bytes=sram, arena_bytes=budget)
    check_arena_budget(sram, budget)

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

    raise VelaConversionError(f"Could not find Vela output in {work_dir}. Checked: {candidates}")
