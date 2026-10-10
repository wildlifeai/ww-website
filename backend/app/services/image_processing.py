# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Image processing helpers — thumbnails, previews, and animal crops.

Pure, synchronous Pillow operations on in-memory bytes (no network, no I/O), so
they are easy to unit-test and can be dispatched to a thread by callers. Used by
the Media Registry to produce CDN renditions and DINOv3 crop inputs.
"""

from __future__ import annotations

from io import BytesIO

import structlog

try:
    from PIL import Image
except ImportError as e:  # pragma: no cover
    raise RuntimeError("Pillow is required for image processing.") from e

logger = structlog.get_logger()


def get_dimensions(data: bytes) -> tuple[int, int]:
    """Return (width, height) of an encoded image."""
    with Image.open(BytesIO(data)) as img:
        return img.width, img.height


def to_jpeg(data: bytes, quality: int = 90) -> bytes:
    """Re-encode any Pillow-readable image (e.g. a raw BMP) to JPEG at full size.

    Used by the upload path to compress device BMP frames in-pipeline at a
    controlled quality (better than the camera's aggressive on-device JPEG)
    without downscaling. Raises if the bytes aren't a decodable image.
    """
    with Image.open(BytesIO(data)) as img:
        img = img.convert("RGB")
        out = BytesIO()
        img.save(out, format="JPEG", quality=quality)
        return out.getvalue()


def resize_to_max(data: bytes, max_size: int, quality: int = 85) -> bytes:
    """Downscale so the longest edge is ``max_size`` px; never upscale.

    Returns JPEG bytes. Images already within bounds are re-encoded at the given
    quality (so output is always a normalised JPEG).
    """
    with Image.open(BytesIO(data)) as img:
        img = img.convert("RGB")
        w, h = img.size
        scale = min(1.0, max_size / max(w, h))
        if scale < 1.0:
            img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)
        out = BytesIO()
        img.save(out, format="JPEG", quality=quality)
        return out.getvalue()


def crop_bbox(
    data: bytes,
    bbox: tuple[float, float, float, float],
    padding: float = 0.1,
    quality: int = 90,
) -> bytes:
    """Crop a normalised bbox ``(x, y, w, h)`` (0-1) with proportional padding.

    Padding expands the box by ``padding`` × its own width/height on each side,
    clamped to the image. Returns JPEG bytes. Used to feed DINOv3 the animal
    region rather than the full frame.
    """
    x, y, w, h = bbox
    with Image.open(BytesIO(data)) as img:
        img = img.convert("RGB")
        width, height = img.size

        left = max(0.0, x - w * padding)
        top = max(0.0, y - h * padding)
        right = min(1.0, x + w + w * padding)
        bottom = min(1.0, y + h + h * padding)

        box = [round(left * width), round(top * height), round(right * width), round(bottom * height)]
        # Guarantee a non-empty crop region.
        if box[2] <= box[0]:
            box[2] = min(width, box[0] + 1)
        if box[3] <= box[1]:
            box[3] = min(height, box[1] + 1)

        crop = img.crop((box[0], box[1], box[2], box[3]))
        out = BytesIO()
        crop.save(out, format="JPEG", quality=quality)
        return out.getvalue()
