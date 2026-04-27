# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the model domain — parsing, label extraction, filename generation."""

import tempfile
from pathlib import Path

import pytest

from app.domain.model import (
    ModelDomainError,
    _build_firmware_filename,
    _extract_labels_from_header,
    _parse_model_zip_name,
)


class TestParseModelZipName:
    def test_standard_format(self):
        name, version = _parse_model_zip_name("mymodel-custom-1.0.0.zip")
        assert name == "mymodel"
        assert version == "1.0.0"

    def test_complex_name(self):
        name, version = _parse_model_zip_name("bird-detector-custom-2.1.zip")
        assert name == "bird-detector"
        assert version == "2.1"

    def test_no_custom_tag(self):
        """Should fallback to defaults."""
        name, version = _parse_model_zip_name("mymodel.zip")
        assert name == "unknown"
        assert version == "1.0.0"

    def test_not_a_zip(self):
        with pytest.raises(ValueError, match="must end with .zip"):
            _parse_model_zip_name("model.tar.gz")


class TestExtractLabels:
    def test_valid_header(self):
        """Labels should be extracted from model_variables.h."""
        content = '''
        const char* ei_classifier_inferencing_categories[] = { "cat", "dog", "bird" };
        '''
        with tempfile.NamedTemporaryFile(mode="w", suffix=".h", delete=False) as f:
            f.write(content)
            f.flush()
            labels = _extract_labels_from_header(Path(f.name))

        assert labels == ["cat", "dog", "bird"]

    def test_no_labels_raises(self):
        content = "// no labels here"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".h", delete=False) as f:
            f.write(content)
            f.flush()
            with pytest.raises(ModelDomainError, match="No labels"):
                _extract_labels_from_header(Path(f.name))


class TestBuildFirmwareFilename:
    def test_standard_ids(self):
        content = '''
        .project_id = 12345
        .deploy_version = 3
        '''
        with tempfile.NamedTemporaryFile(mode="w", suffix=".h", delete=False) as f:
            f.write(content)
            f.flush()
            name = _build_firmware_filename(Path(f.name))

        assert name == "12345V3.tfl"

    def test_truncation(self):
        """Filenames longer than 8 chars get truncated for 8.3 compliance."""
        content = '''
        .project_id = 123456789
        .deploy_version = 99
        '''
        with tempfile.NamedTemporaryFile(mode="w", suffix=".h", delete=False) as f:
            f.write(content)
            f.flush()
            name = _build_firmware_filename(Path(f.name))

        # "123456789V99" → truncated to "12345678.tfl"
        assert len(name) <= 12  # 8 + ".tfl"
        assert name.endswith(".tfl")

    def test_fallback(self):
        """Missing IDs should fallback to MOD00001.tfl."""
        content = "// nothing useful"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".h", delete=False) as f:
            f.write(content)
            f.flush()
            name = _build_firmware_filename(Path(f.name))

        assert name == "MOD00001.tfl"
