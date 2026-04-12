# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the manifest domain — helpers and hex array extraction."""

from app.domain.manifest import _extract_hex_array, _flatten_directory, ManifestDomainError
import pytest
import tempfile
from pathlib import Path


class TestExtractHexArray:
    def test_valid_c_array(self):
        """Standard C array should be parsed to bytes."""
        c_code = '''
        const unsigned char model_data[] = { 0x00, 0x01, 0xFF, 0xAB };
        '''
        result = _extract_hex_array(c_code)
        assert result == bytes([0x00, 0x01, 0xFF, 0xAB])

    def test_multiline_array(self):
        c_code = '''
        const unsigned char model_data[] = {
            0x00, 0x01,
            0x02, 0x03
        };
        '''
        result = _extract_hex_array(c_code)
        assert result == bytes([0x00, 0x01, 0x02, 0x03])

    def test_no_array_raises(self):
        with pytest.raises(ManifestDomainError, match="Could not find"):
            _extract_hex_array("// no array here")

    def test_empty_array_raises(self):
        c_code = "const unsigned char data[] = { /* empty */ };"
        with pytest.raises(ManifestDomainError, match="No hex values"):
            _extract_hex_array(c_code)


class TestFlattenDirectory:
    def test_flattens_nested_files(self):
        """Files in subdirectories should be moved to root."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sub = root / "nested" / "deep"
            sub.mkdir(parents=True)
            (sub / "file.txt").write_text("hello")
            (root / "root_file.txt").write_text("root")

            _flatten_directory(root)

            assert (root / "file.txt").exists()
            assert (root / "root_file.txt").exists()
            assert not (root / "nested").exists()

    def test_empty_directory(self):
        """Flattening an empty dir should not raise."""
        with tempfile.TemporaryDirectory() as td:
            _flatten_directory(Path(td))

    def test_duplicate_names_overwritten(self):
        """If nested file has same name as root file, root gets overwritten."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sub = root / "sub"
            sub.mkdir()
            (root / "file.txt").write_text("root version")
            (sub / "file.txt").write_text("nested version")

            _flatten_directory(root)

            content = (root / "file.txt").read_text()
            assert content == "nested version"
