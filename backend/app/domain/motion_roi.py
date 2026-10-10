# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Motion ROI — dependency-free animal localisation from a frame sequence.

Given a short burst of frames (the WW500 captures several pictures per trigger,
and the alternating BMP/JPEG diagnostic mode yields near-pairs), this finds the
bounding box of whatever *moved* by frame-differencing against a reference
frame: absdiff → threshold → morphology → connected-component → temporal gating.

It is pure NumPy + Pillow — no OpenCV, scipy, skimage, or torch — so it runs on
the lean dev-cloud image where SpeciesNet is unavailable. Uses:
  - a SpeciesNet-free crop source for `ANIMAL_CROP` (so DINOv3 still gets a crop),
  - a cheap blank / false-trigger filter (``motion_frac`` near zero ⇒ empty frame),
  - ROI-cropped burst clustering that ignores the static background.

Three entry points:
  - :func:`compute_motion_roi` — one union ROI across the whole sequence.
  - :func:`compute_motion_roi_per_frame` — a tracked ROI per frame.
  - :func:`compute_motion_fractions`, the raw changed-pixel fraction per frame
    (the ``motion_frac`` evidence signal, no box).

Salvaged from the deprecated ``v2-migration/cluster-progression-dev`` branch
(commit 6d7278bc, "Clustering motion ROI added") and extracted into a reusable
module. Thresholds were tuned on that branch's frames and should be re-checked
against the WW500's grayscale night/IR captures before being relied upon.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
from PIL import Image


def _to_grayscale_small(img: Image.Image, size: Tuple[int, int]) -> np.ndarray:
    return np.asarray(img.convert("L").resize(size, Image.Resampling.BILINEAR), dtype=np.uint8)


def _binary_open_close(mask: np.ndarray) -> np.ndarray:
    """Tiny morphology to reduce speckle (no extra deps).

    This is intentionally simple and fast on the small motion mask.
    """

    # 3x3 erosion then dilation (open) then dilation+erosion (close).
    # Implemented with summed-area via neighbor AND/OR.
    m = mask

    def erode(x: np.ndarray) -> np.ndarray:
        y = x.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                y &= np.roll(np.roll(x, dy, axis=0), dx, axis=1)
        # zero-wrap artifacts: clear borders
        y[0, :] = False
        y[-1, :] = False
        y[:, 0] = False
        y[:, -1] = False
        return y

    def dilate(x: np.ndarray) -> np.ndarray:
        y = x.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                y |= np.roll(np.roll(x, dy, axis=0), dx, axis=1)
        y[0, :] = False
        y[-1, :] = False
        y[:, 0] = False
        y[:, -1] = False
        return y

    m = dilate(erode(m))
    m = erode(dilate(m))
    return m


def _small_to_full_box(box_s: Tuple[int, int, int, int], *, sx: float, sy: float, w: int, h: int) -> Tuple[int, int, int, int]:
    x0s, y0s, x1s, y1s = box_s
    x0 = int(round(x0s * sx))
    y0 = int(round(y0s * sy))
    x1 = int(round((x1s + 1) * sx))
    y1 = int(round((y1s + 1) * sy))
    x0 = max(0, min(w - 1, x0))
    y0 = max(0, min(h - 1, y0))
    x1 = max(x0 + 1, min(w, x1))
    y1 = max(y0 + 1, min(h, y1))
    return (x0, y0, x1, y1)


def _dominant_component_bbox(
    mask: np.ndarray,
    *,
    prefer: Optional[Tuple[int, int, int, int]] = None,
    max_step_frac: float = 0.35,
) -> Optional[Tuple[int, int, int, int]]:
    """Return bbox in small coords for the best connected component.

    Selection strategy:
      - If `prefer` is provided, pick the component closest to its center, but
        only if the step isn't ridiculous (gating).
      - Otherwise, pick the largest component.

    Uses a simple flood-fill on the small mask (fast at 320x240).
    """
    H, W = mask.shape
    visited = np.zeros_like(mask, dtype=np.uint8)

    def center(box: Tuple[int, int, int, int]) -> Tuple[float, float]:
        x0, y0, x1, y1 = box
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)

    prefer_c = center(prefer) if prefer is not None else None
    max_step = max_step_frac * float(min(W, H))

    best_box = None
    best_score = None

    # Iterate pixels; flood fill components
    for y in range(1, H - 1):
        row = mask[y]
        for x in range(1, W - 1):
            if not row[x] or visited[y, x]:
                continue

            # BFS stack
            stack = [(x, y)]
            visited[y, x] = 1
            x0 = x1 = x
            y0 = y1 = y
            area = 0

            while stack:
                cx, cy = stack.pop()
                area += 1
                if cx < x0:
                    x0 = cx
                if cx > x1:
                    x1 = cx
                if cy < y0:
                    y0 = cy
                if cy > y1:
                    y1 = cy

                # 4-neighborhood
                for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                    if 0 < nx < W - 1 and 0 < ny < H - 1 and mask[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = 1
                        stack.append((nx, ny))

            box = (x0, y0, x1, y1)

            if prefer_c is None:
                score = float(area)
            else:
                cx, cy = center(box)
                dx = cx - prefer_c[0]
                dy = cy - prefer_c[1]
                dist = float((dx * dx + dy * dy) ** 0.5)
                # Gate out implausible jumps (likely wrong component)
                if dist > max_step:
                    continue
                # Prefer closer components, but still bias toward non-trivial area
                score = float(area) / (1.0 + dist)

            if best_score is None or score > best_score:
                best_score = score
                best_box = box

    return best_box


def compute_motion_roi(
    images: List[Optional[Image.Image]],
    *,
    small_size: Tuple[int, int] = (320, 240),
    diff_threshold: int = 15,
    min_motion_frac: float = 0.001,
    max_motion_frac: float = 0.6,
    pad_frac: float = 0.15,
) -> Optional[Tuple[int, int, int, int]]:
    """Compute a single ROI bbox based on motion across a short sequence.

    Notes:
      - Uses absdiff vs the first valid frame.
      - Returns None if motion is too small (noise) or too large (camera shift).
      - bbox is returned in full-resolution coordinates.
    """

    valid = [im for im in images if im is not None]
    if len(valid) < 2:
        return None

    ref_full = valid[0]
    ref_small = _to_grayscale_small(ref_full, small_size).astype(np.int16)

    union = None
    for im in valid[1:]:
        cur = _to_grayscale_small(im, small_size).astype(np.int16)
        diff = np.abs(cur - ref_small)
        mask = diff > diff_threshold
        union = mask if union is None else (union | mask)

    if union is None:
        return None

    motion_frac = float(union.mean())
    if motion_frac < min_motion_frac or motion_frac > max_motion_frac:
        return None

    ys, xs = np.where(union)
    if xs.size == 0:
        return None

    x0s, x1s = int(xs.min()), int(xs.max())
    y0s, y1s = int(ys.min()), int(ys.max())

    # padding in small coords
    bw = x1s - x0s + 1
    bh = y1s - y0s + 1
    px = int(bw * pad_frac)
    py = int(bh * pad_frac)

    W, H = small_size
    x0s = max(0, x0s - px)
    y0s = max(0, y0s - py)
    x1s = min(W - 1, x1s + px)
    y1s = min(H - 1, y1s + py)

    # map to full-res (use ref_full aspect)
    sx = ref_full.width / float(W)
    sy = ref_full.height / float(H)
    x0 = int(round(x0s * sx))
    y0 = int(round(y0s * sy))
    x1 = int(round((x1s + 1) * sx))
    y1 = int(round((y1s + 1) * sy))

    # clamp
    x0 = max(0, min(ref_full.width - 1, x0))
    y0 = max(0, min(ref_full.height - 1, y0))
    x1 = max(x0 + 1, min(ref_full.width, x1))
    y1 = max(y0 + 1, min(ref_full.height, y1))

    return (x0, y0, x1, y1)


def compute_motion_fractions(
    images: List[Optional[Image.Image]],
    *,
    small_size: Tuple[int, int] = (320, 240),
    diff_threshold: int = 15,
) -> List[float]:
    """Fraction of low-res pixels that changed per frame, the ``motion_frac`` evidence signal.

    Same greyscale absdiff and threshold as :func:`compute_motion_roi_per_frame`
    (which keeps the fraction internal and returns boxes), but returned raw, with no
    noise floor, no camera-shift ceiling and no carry-forward, so a still frame after
    a moving one reads 0. Every frame is differenced against the first valid frame;
    the reference frame itself is differenced against the second so it is not 0 by
    construction. Missing frames and singletons read 0.
    """
    n = len(images)
    out = [0.0] * n
    valid = [i for i, im in enumerate(images) if im is not None]
    if len(valid) < 2:
        return out
    smalls = {i: _to_grayscale_small(images[i], small_size).astype(np.int16) for i in valid}
    ref = valid[0]
    for i in valid:
        other = smalls[valid[1]] if i == ref else smalls[ref]
        out[i] = float((np.abs(smalls[i] - other) > diff_threshold).mean())
    return out


def compute_motion_roi_per_frame(
    images: List[Optional[Image.Image]],
    *,
    small_size: Tuple[int, int] = (320, 240),
    diff_threshold: int = 15,
    min_motion_frac: float = 0.001,
    max_motion_frac: float = 0.6,
    pad_frac: float = 0.12,
    max_step_frac: float = 0.35,
) -> List[Optional[Tuple[int, int, int, int]]]:
    """Compute a motion ROI bbox for each frame.

    Efficient design:
      - Work at low res for full-frame diffs.
      - Extract the dominant connected component per frame.
      - Use temporal gating (prefer component near the previous bbox).

    Returns a list aligned with `images` entries. Each ROI is full-res pixels.
    """

    n = len(images)
    out: List[Optional[Tuple[int, int, int, int]]] = [None] * n

    # Find reference frame (first valid)
    ref_idx = next((i for i, im in enumerate(images) if im is not None), None)
    if ref_idx is None:
        return out

    ref_full = images[ref_idx]
    assert ref_full is not None

    W, H = small_size
    ref_small = _to_grayscale_small(ref_full, small_size).astype(np.int16)

    # Helpers in small coords
    prev_box_s: Optional[Tuple[int, int, int, int]] = None

    sx = ref_full.width / float(W)
    sy = ref_full.height / float(H)

    for i, im in enumerate(images):
        if im is None:
            out[i] = None
            continue

        cur = _to_grayscale_small(im, small_size).astype(np.int16)
        diff = np.abs(cur - ref_small)
        mask = diff > diff_threshold

        motion_frac = float(mask.mean())
        if motion_frac < min_motion_frac or motion_frac > max_motion_frac:
            # If we have a previous bbox, carry it forward (animal paused / brief noise)
            if prev_box_s is not None:
                x0s, y0s, x1s, y1s = prev_box_s
                out[i] = _small_to_full_box((x0s, y0s, x1s, y1s), sx=sx, sy=sy, w=im.width, h=im.height)
            else:
                out[i] = None
            continue

        mask = _binary_open_close(mask)

        box_s = _dominant_component_bbox(mask, prefer=prev_box_s, max_step_frac=max_step_frac)
        if box_s is None:
            if prev_box_s is not None:
                x0s, y0s, x1s, y1s = prev_box_s
                out[i] = _small_to_full_box((x0s, y0s, x1s, y1s), sx=sx, sy=sy, w=im.width, h=im.height)
            else:
                out[i] = None
            continue

        # Pad in small coords
        x0s, y0s, x1s, y1s = box_s
        bw = x1s - x0s + 1
        bh = y1s - y0s + 1
        px = int(bw * pad_frac)
        py = int(bh * pad_frac)
        x0s = max(0, x0s - px)
        y0s = max(0, y0s - py)
        x1s = min(W - 1, x1s + px)
        y1s = min(H - 1, y1s + py)
        prev_box_s = (x0s, y0s, x1s, y1s)

        out[i] = _small_to_full_box(prev_box_s, sx=sx, sy=sy, w=im.width, h=im.height)

    return out
