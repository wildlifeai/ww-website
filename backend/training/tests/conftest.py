# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The container's own tests import its modules by path; run with ``pytest backend/training/tests``."""

import sys
from pathlib import Path

TRAINING_DIR = Path(__file__).resolve().parents[1]
if str(TRAINING_DIR) not in sys.path:
    sys.path.insert(0, str(TRAINING_DIR))
