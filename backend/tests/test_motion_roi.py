# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.domain.clustering import compute_motion_roi, compute_motion_roi_per_frame

try:
    from PIL import Image
except Exception as e:  # pragma: no cover
    raise RuntimeError("Pillow is required to run ROI tests") from e


def _mk_frame(size=(320, 240), square=None, brightness=0, noise=0, seed=0):
    """Create a synthetic RGB frame.

    square: (x0,y0,x1,y1) region to paint white
    brightness: value added to all pixels (clamped)
    noise: stddev of gaussian noise
    """

    rng = np.random.default_rng(seed)
    arr = np.zeros((size[1], size[0], 3), dtype=np.float32)

    if square is not None:
        x0, y0, x1, y1 = square
        arr[y0:y1, x0:x1, :] = 255.0

    if brightness:
        arr += float(brightness)

    if noise:
        arr += rng.normal(0, float(noise), size=arr.shape)

    arr = np.clip(arr, 0, 255).astype(np.uint8)
    # Pillow warns that the `mode` parameter is deprecated; omit it.
    return Image.fromarray(arr)


def test_motion_roi_no_motion_returns_none():
    im = _mk_frame()
    roi = compute_motion_roi([im, im.copy(), im.copy()], diff_threshold=10)
    assert roi is None


def test_motion_roi_global_brightness_change_rejected():
    im0 = _mk_frame()
    im1 = _mk_frame(brightness=25)
    roi = compute_motion_roi([im0, im1], diff_threshold=10, max_motion_frac=0.2)
    assert roi is None


def test_motion_roi_moving_square_detected_and_reasonable_size():
    # Square moves slightly between frames.
    im0 = _mk_frame(square=(120, 90, 160, 130))
    im1 = _mk_frame(square=(130, 90, 170, 130))
    im2 = _mk_frame(square=(140, 95, 180, 135))

    roi = compute_motion_roi(
        [im0, im1, im2],
        small_size=(320, 240),
        diff_threshold=10,
        min_motion_frac=0.0005,
        max_motion_frac=0.6,
        pad_frac=0.10,
    )
    assert roi is not None
    x0, y0, x1, y1 = roi

    # bbox should contain the square region generally
    assert x0 < 130 and y0 < 95
    assert x1 > 170 and y1 > 130

    # bbox should not be close to full frame
    area_frac = ((x1 - x0) * (y1 - y0)) / float(im0.width * im0.height)
    assert 0.01 <= area_frac <= 0.5


def test_motion_roi_per_frame_tracks_moving_square():
    # Square moves right each frame. We expect per-frame bboxes to shift.
    ims = [
        _mk_frame(size=(640, 480), square=(120 + i * 30, 160, 180 + i * 30, 220))
        for i in range(6)
    ]

    rois = compute_motion_roi_per_frame(
        ims,
        small_size=(320, 240),
        diff_threshold=10,
        min_motion_frac=0.0005,
        max_motion_frac=0.6,
        pad_frac=0.10,
    )
    assert len(rois) == len(ims)
    assert any(r is not None for r in rois[1:])

    centers = []
    for r in rois:
        if r is None:
            continue
        x0, _, x1, _ = r
        centers.append((x0 + x1) / 2.0)

    assert len(centers) >= 3
    assert centers[-1] > centers[0]

    unique = set(tuple(r) for r in rois if r is not None)
    assert len(unique) >= 2


@pytest.mark.parametrize("noise", [5, 10, 15])
def test_motion_roi_noise_only_rejected_by_min_motion_frac(noise):
    im0 = _mk_frame(noise=noise, seed=1)
    im1 = _mk_frame(noise=noise, seed=2)
    roi = compute_motion_roi(
        [im0, im1],
        diff_threshold=25,  # higher threshold to avoid noise
        min_motion_frac=0.005,
    )
    assert roi is None


def test_motion_roi_example_images_fixture_if_present():
    """Regression-style test using repo example frames.

    This test is skipped if the example_images folder isn't present.
    """

    root = Path(__file__).resolve().parents[2]
    ex_dir = root / "example_images"
    if not ex_dir.exists():
        pytest.skip("example_images folder not present")

    paths = sorted(ex_dir.glob("*.jpg"))
    if len(paths) < 2:
        pytest.skip("not enough example images")

    imgs = [Image.open(p).convert("RGB") for p in paths[:10]]
    roi = compute_motion_roi(imgs, diff_threshold=15, min_motion_frac=0.001, max_motion_frac=0.6)
    assert roi is not None

    x0, y0, x1, y1 = roi
    w, h = imgs[0].width, imgs[0].height

    # Basic sanity: ROI within bounds and non-trivial
    assert 0 <= x0 < x1 <= w
    assert 0 <= y0 < y1 <= h

    area_frac = ((x1 - x0) * (y1 - y0)) / float(w * h)
    assert 0.01 <= area_frac <= 0.8
