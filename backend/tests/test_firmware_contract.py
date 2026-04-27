# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contract tests for the firmware filename format.

The edge device firmware loads AI models by 8.3 filename on the SD card.
The filename format {firmware_model_id}V{version}.TFL is a hard contract —
if this format changes, all deployed devices will fail to load models.

These tests enforce that invariant.
"""



def generate_firmware_filename(firmware_model_id: int, version: int) -> str:
    """Generate the 8.3-compliant firmware filename.

    This is the canonical implementation — used by manifest.py and
    convert_model_job to produce deterministic filenames.

    Args:
        firmware_model_id: Globally unique integer from ai_model_families.
        version: Sequential integer from next_version_number().

    Returns:
        Uppercase 8.3 filename, e.g. "42V3.TFL"
    """
    stem = f"{firmware_model_id}V{version}"
    if len(stem) > 8:
        stem = stem[:8]
    return f"{stem}.TFL"


class TestFirmwareFilenameContract:
    """Enforce the firmware filename contract: {fwid}V{ver}.TFL"""

    def test_standard_ids(self):
        assert generate_firmware_filename(1, 1) == "1V1.TFL"

    def test_multi_digit_ids(self):
        assert generate_firmware_filename(42, 3) == "42V3.TFL"

    def test_large_model_id(self):
        assert generate_firmware_filename(100, 5) == "100V5.TFL"

    def test_double_digit_version(self):
        assert generate_firmware_filename(1, 12) == "1V12.TFL"

    def test_truncation_8_3_compliance(self):
        """Filenames longer than 8 chars in the stem get truncated."""
        name = generate_firmware_filename(123456, 99)
        stem = name.replace(".TFL", "")
        assert len(stem) <= 8, f"Stem '{stem}' exceeds 8 chars"
        assert len(name) <= 12, f"Full name '{name}' exceeds 8.3 format"

    def test_always_uppercase(self):
        name = generate_firmware_filename(5, 2)
        assert name == name.upper(), f"Filename '{name}' is not uppercase"

    def test_extension_is_tfl(self):
        name = generate_firmware_filename(1, 1)
        assert name.endswith(".TFL"), f"Filename '{name}' does not end with .TFL"

    def test_stem_contains_only_digits_and_v(self):
        """8.3 FAT filenames must not contain special characters."""
        name = generate_firmware_filename(42, 7)
        stem = name.replace(".TFL", "")
        for ch in stem:
            assert ch.isdigit() or ch == "V", f"Invalid char '{ch}' in stem '{stem}'"

    def test_v_separator_present(self):
        name = generate_firmware_filename(10, 3)
        stem = name.replace(".TFL", "")
        assert "V" in stem, f"Stem '{stem}' missing V separator"

    def test_zero_ids_produce_valid_filename(self):
        """Edge case: IDs starting from 0 (unlikely but shouldn't crash)."""
        name = generate_firmware_filename(0, 1)
        assert name == "0V1.TFL"

    def test_boundary_8_char_stem(self):
        """Exactly 8 chars in stem should not be truncated."""
        # 12345V78 = 8 chars
        name = generate_firmware_filename(12345, 78)
        assert name == "12345V78.TFL"

    def test_boundary_9_char_stem_truncated(self):
        """9 chars in stem should be truncated to 8."""
        # 123456V78 = 9 chars → truncate to 12345678
        name = generate_firmware_filename(123456, 78)
        stem = name.replace(".TFL", "")
        assert len(stem) == 8
