# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The tensor-arena check in services/vela.py: Vela's SRAM report against MODEL_ARENA_BYTES."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import vela
from app.services.vela import (
    ArenaBudgetExceeded,
    VelaConversionError,
    check_arena_budget,
    check_compiled_model,
    compiled_sram_bytes,
    parse_sram_used_bytes,
    run_vela_conversion,
)

CSV = "experiment,network,sram_memory_used,off_chip_flash_memory_used\ndefault,model_int8,{sram},412.25\n"


class TestParse:
    def test_summary_csv_is_kib(self):
        assert parse_sram_used_bytes(CSV.format(sram="231.5")) == 237056

    def test_console_line_is_the_fallback(self):
        out = "Network summary for model_int8\nTotal SRAM used                          231.50 KiB\n"
        assert parse_sram_used_bytes(None, out) == 237056
        assert parse_sram_used_bytes(CSV.format(sram=""), out) == 237056

    def test_nothing_reported(self):
        assert parse_sram_used_bytes(None, "no summary") is None
        assert parse_sram_used_bytes("", "") is None
        assert parse_sram_used_bytes(CSV.format(sram="n/a")) is None


class TestBudget:
    def test_within_and_at_the_limit(self):
        check_arena_budget(100, 512 * 1024)
        check_arena_budget(512 * 1024, 512 * 1024)

    def test_over_the_limit_is_refused(self):
        with pytest.raises(ArenaBudgetExceeded, match="tensor arena is 512 KiB"):
            check_arena_budget(512 * 1024 + 1, 512 * 1024)

    def test_unknown_only_logs(self):
        check_arena_budget(None, 512 * 1024)

    def test_refusal_is_a_vela_error_so_every_caller_surfaces_it(self):
        assert issubclass(ArenaBudgetExceeded, VelaConversionError)


def _fake_vela(monkeypatch, sram_kib):
    """subprocess.run that writes what Vela writes: the compiled model and the summary CSV."""

    def run(cmd, **kwargs):
        out_dir = cmd[cmd.index("--output-dir") + 1]
        stem = Path(cmd[-1]).stem
        (Path(out_dir) / f"{stem}_vela.tflite").write_bytes(b"compiled")
        if sram_kib is not None:
            (Path(out_dir) / f"{stem}_summary_internal-default.csv").write_text(CSV.format(sram=sram_kib))
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(vela.subprocess, "run", run)


class TestRunVelaConversion:
    async def test_model_that_fits_is_returned(self, tmp_path, monkeypatch):
        _fake_vela(monkeypatch, "300.0")
        src = tmp_path / "model_int8.tflite"
        src.write_bytes(b"x")
        out = await run_vela_conversion(src, tmp_path)
        assert out.name == "model_int8_vela.tflite"

    async def test_model_over_the_arena_is_refused(self, tmp_path, monkeypatch):
        _fake_vela(monkeypatch, "600.0")
        src = tmp_path / "model_int8.tflite"
        src.write_bytes(b"x")
        with pytest.raises(ArenaBudgetExceeded, match="600.0 KiB"):
            await run_vela_conversion(src, tmp_path)

    async def test_budget_comes_from_settings(self, tmp_path, monkeypatch):
        _fake_vela(monkeypatch, "300.0")
        monkeypatch.setattr(vela.settings, "MODEL_ARENA_BYTES", 256 * 1024)
        src = tmp_path / "m.tflite"
        src.write_bytes(b"x")
        with pytest.raises(ArenaBudgetExceeded):
            await run_vela_conversion(src, tmp_path)
        assert (await run_vela_conversion(src, tmp_path, arena_bytes=512 * 1024)).name == "m_vela.tflite"

    async def test_no_report_does_not_block(self, tmp_path, monkeypatch):
        _fake_vela(monkeypatch, None)
        src = tmp_path / "m.tflite"
        src.write_bytes(b"x")
        assert (await run_vela_conversion(src, tmp_path)).name == "m_vela.tflite"

    async def test_upload_path_surfaces_the_refusal(self, tmp_path, monkeypatch):
        """convert_uploaded_model turns it into the ModelDomainError the upload job reports."""
        import io
        import zipfile

        from app.domain.model import ModelDomainError, convert_uploaded_model

        _fake_vela(monkeypatch, "700.0")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("trained.tflite", b"x")
        with pytest.raises(ModelDomainError, match="tensor arena"):
            await convert_uploaded_model(buf.getvalue(), "rat-custom-v1.zip")


def _compiled(tmp_path, scratch_bytes, name="1V1.tfl"):
    from tests.tflite_fixtures import build_classifier_tflite

    path = tmp_path / name
    path.write_bytes(build_classifier_tflite(input_shape=[1, 96, 96, 1], output_classes=2, scratch_bytes=scratch_bytes))
    return path


class TestCompiledModel:
    """Precompiled uploads and catalogue models skip Vela here, so the arena is read from the file."""

    def test_scratch_tensor_is_the_sram_and_scratch_fast_is_not_added(self, tmp_path):
        assert compiled_sram_bytes(_compiled(tmp_path, 74096)) == 74096

    def test_no_scratch_tensor_or_no_model_is_unknown(self, tmp_path):
        assert compiled_sram_bytes(_compiled(tmp_path, None)) is None
        junk = tmp_path / "7V1.tfl"
        junk.write_bytes(b"PK\x03\x04 a zip, not a model")
        assert compiled_sram_bytes(junk) is None

    def test_over_the_arena_is_refused(self, tmp_path):
        check_compiled_model(_compiled(tmp_path, 264512))  # Edge Impulse YOLOv5 192 px, dev bucket
        with pytest.raises(ArenaBudgetExceeded, match="1051.3 KiB"):
            check_compiled_model(_compiled(tmp_path, 1076544))  # YOLOv11 192 px, dev bucket

    async def test_precompiled_upload_surfaces_the_refusal(self, tmp_path):
        import io
        import zipfile

        from app.domain.model import ModelDomainError, convert_uploaded_model

        def package(scratch_bytes):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr("1V1.tfl", _compiled(tmp_path, scratch_bytes).read_bytes())
                z.writestr("labels.txt", "no person\nperson\n")
            return buf.getvalue()

        tfl, _, labels = await convert_uploaded_model(package(74096), "person-custom-v1.zip")
        assert labels == ["no person", "person"] and tfl
        with pytest.raises(ModelDomainError, match="tensor arena"):
            await convert_uploaded_model(package(1076544), "person-custom-v2.zip")


def test_default_is_the_firmware_reservation():
    from app.config import Settings

    assert Settings.model_fields["MODEL_ARENA_BYTES"].default == 512 * 1024
