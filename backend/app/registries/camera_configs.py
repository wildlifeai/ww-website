# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Camera configuration registry — extracted from app.py L82-93."""

CAMERA_CONFIGS = {
    "Raspberry Pi": {
        "description": "Standard configuration (RPi v1/v2/v3)",
        "url": None,  # Fetched from DB 'latest' firmware record
        "filename": "CONFIG.TXT",
    },
    "HM0360": {
        "description": "Configuration for Himax HM0360 sensor",
        "url": "https://raw.githubusercontent.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/main/_Tools/hm0360_md_medium.txt",
        "filename": "CONFIG.TXT",
    },
}
