# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Image clustering domain — near-duplicate detection via perceptual hashing.

Clusters camera trap images by visual similarity using dHash (difference hash)
and a BK-tree for scalable nearest-neighbor search. Selects the sharpest image
per cluster as the representative for iNaturalist upload.

Ported from dev-inat-api:cluster_utils.py and adapted for the V2 pipeline
(operates on in-memory image bytes, not filesystem paths).
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    from PIL import Image
except ImportError as e:  # pragma: no cover
    raise RuntimeError("Pillow is required for clustering.") from e

try:
    import structlog

    logger = structlog.get_logger()
except Exception:  # pragma: no cover
    import logging

    logger = logging.getLogger(__name__)

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


# ── Data structures ──────────────────────────────────────────────────


@dataclass
class ImageRecord:
    """Metadata for a single image in the clustering pipeline."""

    filename: str
    dhash: int
    sharpness: float
    width: int
    height: int
    roi: Optional[Tuple[int, int, int, int]] = None  # (x0,y0,x1,y1) in full-res pixels
    index: int = 0  # position in the original input list


@dataclass
class RoiPreview:
    """Optional visual ROI preview artifacts for frontend verification."""

    filename: str
    roi: Tuple[int, int, int, int]
    overlay_png_base64: str  # full frame with ROI rectangle drawn
    crop_png_base64: str  # cropped ROI image


@dataclass
class ClusterResult:
    """Result of the clustering pipeline."""

    records: List[ImageRecord]
    clusters: Dict[int, List[int]]  # root_idx -> [member indices]
    representatives: Dict[int, int]  # root_idx -> representative idx
    roi_preview: Optional[RoiPreview] = None
    total_images: int = 0
    total_clusters: int = 0
    total_representatives: int = 0


# ── Perceptual hashing ───────────────────────────────────────────────


def _to_grayscale_small(img: Image.Image, size: Tuple[int, int]) -> np.ndarray:
    return np.asarray(img.convert("L").resize(size, Image.BILINEAR), dtype=np.uint8)


def compute_dhash(img: Image.Image, hash_size: int = 8) -> int:
    """Difference hash (dHash) — returns a 64-bit int when hash_size=8.

    Compares adjacent pixels horizontally to produce a compact
    perceptual fingerprint that is robust to scaling and compression.
    """
    gray = _to_grayscale_small(img, (hash_size + 1, hash_size))
    diff = gray[:, 1:] > gray[:, :-1]

    bits = diff.flatten().astype(np.uint8)
    out = 0
    for b in bits:
        out = (out << 1) | int(b)
    return out


def hamming_distance64(a: int, b: int) -> int:
    """Hamming distance between two 64-bit hashes."""
    return (a ^ b).bit_count()


def laplacian_variance_sharpness(img: Image.Image) -> float:
    """Sharpness metric via Laplacian variance — higher = sharper.

    Uses a 3x3 Laplacian kernel implemented with numpy (no OpenCV needed).
    """
    g = np.asarray(img.convert("L"), dtype=np.float32)
    if g.size == 0:
        return 0.0

    if g.shape[0] < 3 or g.shape[1] < 3:
        return float(np.var(g))

    # 3x3 Laplacian convolution (valid region)
    sub = (
        g[:-2, 1:-1]        # top
        + g[1:-1, :-2]      # left
        + (-4) * g[1:-1, 1:-1]  # center
        + g[1:-1, 2:]       # right
        + g[2:, 1:-1]       # bottom
    )
    return float(np.var(sub))


# ── BK-tree for scalable Hamming queries ─────────────────────────────


class BKTree:
    """BK-tree for fast within-distance queries in a metric space.

    Each node stores a hash value and a payload (record index).
    Children are keyed by their distance to the parent.
    """

    class _Node:
        __slots__ = ("value", "payload", "children")

        def __init__(self, value: int, payload: int):
            self.value = value
            self.payload = payload
            self.children: Dict[int, "BKTree._Node"] = {}

    def __init__(self, distance=hamming_distance64):
        self._dist = distance
        self._root: Optional[BKTree._Node] = None

    def add(self, value: int, payload: int) -> None:
        if self._root is None:
            self._root = BKTree._Node(value, payload)
            return

        node = self._root
        while True:
            d = self._dist(value, node.value)
            child = node.children.get(d)
            if child is None:
                node.children[d] = BKTree._Node(value, payload)
                return
            node = child

    def query(self, value: int, max_dist: int) -> List[int]:
        """Return payloads with distance <= max_dist."""
        if self._root is None:
            return []

        out: List[int] = []
        stack = [self._root]
        while stack:
            node = stack.pop()
            d = self._dist(value, node.value)
            if d <= max_dist:
                out.append(node.payload)

            lo = d - max_dist
            hi = d + max_dist
            for cd, child in node.children.items():
                if lo <= cd <= hi:
                    stack.append(child)

        return out


# ── Union-Find ───────────────────────────────────────────────────────


class _UnionFind:
    def __init__(self, n: int):
        self.p = list(range(n))
        self.r = [0] * n

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]  # path compression
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.r[ra] < self.r[rb]:
            self.p[ra] = rb
        elif self.r[ra] > self.r[rb]:
            self.p[rb] = ra
        else:
            self.p[rb] = ra
            self.r[ra] += 1


# ── Core clustering logic ────────────────────────────────────────────


def build_record_from_bytes(
    filename: str, image_bytes: bytes, index: int, hash_size: int = 8
) -> Optional[ImageRecord]:
    """Build an ImageRecord from raw image bytes."""
    try:
        img = Image.open(BytesIO(image_bytes))
        img = img.copy()  # close file handle early

        dh = compute_dhash(img, hash_size=hash_size)
        sharp = laplacian_variance_sharpness(img)

        return ImageRecord(
            filename=filename,
            dhash=dh,
            sharpness=sharp,
            width=img.width,
            height=img.height,
            roi=None,
            index=index,
        )
    except Exception:
        logger.warning("clustering_image_skipped", filename=filename)
        return None


def cluster_by_dhash(
    records: List[ImageRecord], max_hamming: int = 10
) -> Dict[int, List[int]]:
    """Cluster record indices by dHash within a Hamming threshold.

    Uses a BK-tree for O(N log N) average-case performance instead of O(N²).
    """
    n = len(records)
    if n == 0:
        return {}

    uf = _UnionFind(n)
    tree = BKTree(distance=hamming_distance64)

    for i, rec in enumerate(records):
        # Query existing items for neighbors within threshold
        for j in tree.query(rec.dhash, max_hamming):
            uf.union(i, j)
        tree.add(rec.dhash, i)

    clusters: Dict[int, List[int]] = {}
    for i in range(n):
        root = uf.find(i)
        clusters.setdefault(root, []).append(i)

    # Sort by cluster size (largest first)
    return dict(sorted(clusters.items(), key=lambda kv: (-len(kv[1]), kv[0])))


def pick_representatives(
    records: List[ImageRecord], clusters: Dict[int, List[int]]
) -> Dict[int, int]:
    """Pick the sharpest image in each cluster as the representative."""
    reps: Dict[int, int] = {}
    for root, idxs in clusters.items():
        best = max(
            idxs,
            key=lambda i: (records[i].sharpness, records[i].width * records[i].height),
        )
        reps[root] = best
    return reps


# ── Public API ───────────────────────────────────────────────────────


def cluster_images_from_bytes(
    files: List[Tuple[str, bytes]],
    max_hamming: int = 10,
    hash_size: int = 8,
    roi_preview_index: Optional[int] = 0,
    roi_crop_for_hashing: bool = False,
    roi_per_frame: bool = True,
) -> ClusterResult:
    """Cluster uploaded images by visual similarity.

    Args:
        files: List of (filename, image_bytes) tuples.
        max_hamming: Maximum Hamming distance to consider images "similar".
                     Lower = stricter (more clusters). Higher = more merging.
        hash_size: dHash size. 8 produces a 64-bit hash (recommended).

    Returns:
        ClusterResult with records, clusters, and representatives.
    """
    # Build records and also keep PIL images in-memory (for ROI preview and ROI-driven hashing).
    # We don't persist images in ClusterResult; these are ephemeral.
    records: List[ImageRecord] = []
    pil_images: List[Optional[Image.Image]] = []

    for i, (filename, image_bytes) in enumerate(files):
        try:
            img = Image.open(BytesIO(image_bytes))
            img = img.convert("RGB").copy()
        except Exception:
            logger.warning("clustering_image_skipped", filename=filename)
            pil_images.append(None)
            continue

        # Temporarily compute hashes on full frame; we may overwrite below if roi_crop_for_hashing.
        dh = compute_dhash(img, hash_size=hash_size)
        sharp = laplacian_variance_sharpness(img)
        records.append(
            ImageRecord(
                filename=filename,
                dhash=dh,
                sharpness=sharp,
                width=img.width,
                height=img.height,
                roi=None,
                index=i,
            )
        )
        pil_images.append(img)

    # If enabled, compute a per-frame motion ROI and use it to (a) store per-image bbox and
    # (b) compute dHash and sharpness on the cropped region. This helps clustering ignore
    # static background for burst/event frames.
    if roi_crop_for_hashing and len(pil_images) >= 2:
        if roi_per_frame:
            rois = compute_motion_roi_per_frame([im for im in pil_images])
            # Apply per-image crops when possible.
            for rec, im, roi in zip(records, pil_images, rois):
                if im is None or roi is None:
                    continue
                rec.roi = roi
                crop = im.crop(roi)
                rec.dhash = compute_dhash(crop, hash_size=hash_size)
                rec.sharpness = laplacian_variance_sharpness(crop)
        else:
            roi = compute_motion_roi([im for im in pil_images])
            if roi is not None:
                for rec, im in zip(records, pil_images):
                    if im is None:
                        continue
                    rec.roi = roi
                    crop = im.crop(roi)
                    rec.dhash = compute_dhash(crop, hash_size=hash_size)
                    rec.sharpness = laplacian_variance_sharpness(crop)

    clusters = cluster_by_dhash(records, max_hamming=max_hamming)
    reps = pick_representatives(records, clusters)

    roi_preview: Optional[RoiPreview] = None
    if roi_preview_index is not None and 0 <= roi_preview_index < len(pil_images):
        base_img = pil_images[roi_preview_index]
        if base_img is not None and len(pil_images) >= 2:
            roi = compute_motion_roi(pil_images)
            if roi is not None:
                roi_preview = build_roi_preview(
                    filename=records[roi_preview_index].filename,
                    image=base_img,
                    roi=roi,
                )

    logger.info(
        "clustering_complete",
        total_images=len(records),
        total_clusters=len(clusters),
        total_representatives=len(reps),
    )

    return ClusterResult(
        records=records,
        clusters=clusters,
        representatives=reps,
        roi_preview=roi_preview,
        total_images=len(records),
        total_clusters=len(clusters),
        total_representatives=len(reps),
    )


# ── Motion ROI utilities (preview only, not yet used for hashing) ───


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
        bw = (x1s - x0s + 1)
        bh = (y1s - y0s + 1)
        px = int(bw * pad_frac)
        py = int(bh * pad_frac)
        x0s = max(0, x0s - px)
        y0s = max(0, y0s - py)
        x1s = min(W - 1, x1s + px)
        y1s = min(H - 1, y1s + py)
        prev_box_s = (x0s, y0s, x1s, y1s)

        out[i] = _small_to_full_box(prev_box_s, sx=sx, sy=sy, w=im.width, h=im.height)

    return out


def _small_to_full_box(
    box_s: Tuple[int, int, int, int], *, sx: float, sy: float, w: int, h: int
) -> Tuple[int, int, int, int]:
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
    bw = (x1s - x0s + 1)
    bh = (y1s - y0s + 1)
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


def _png_base64(img: Image.Image) -> str:
    import base64

    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def build_roi_preview(
    *,
    filename: str,
    image: Image.Image,
    roi: Tuple[int, int, int, int],
) -> RoiPreview:
    """Build preview images (overlay + cropped) encoded as base64 PNG."""
    from PIL import ImageDraw

    x0, y0, x1, y1 = roi
    overlay = image.copy()
    draw = ImageDraw.Draw(overlay)
    # Draw a visible rectangle
    for w in (0, 1, 2):
        draw.rectangle([x0 - w, y0 - w, x1 + w, y1 + w], outline=(255, 0, 0))

    crop = image.crop((x0, y0, x1, y1))

    return RoiPreview(
        filename=filename,
        roi=roi,
        overlay_png_base64=_png_base64(overlay),
        crop_png_base64=_png_base64(crop),
    )


def build_roi_debug_bundle_zip(
    *,
    files: List[Tuple[str, bytes]],
    max_frames: int = 12,
    roi_params: Optional[Dict[str, object]] = None,
) -> bytes:
    """Build a ZIP containing ROI overlays/crops + a manifest.json.

    This is designed for interactive tuning: users can download the bundle,
    review the crops, and annotate/return it for threshold iteration.

    The ZIP layout:
      manifest.json
      overlay/<filename>.png
      crop/<filename>.png

    Note: we intentionally do NOT include originals to keep size down.
    """

    import json
    import zipfile

    roi_params = roi_params or {}

    # Load PIL images
    pil: List[Tuple[str, Image.Image]] = []
    for name, blob in files[:max_frames]:
        try:
            img = Image.open(BytesIO(blob)).convert("RGB")
            pil.append((name, img))
        except Exception:
            continue

    if len(pil) < 2:
        # Return an empty zip with a manifest explaining the issue
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "error": "not_enough_valid_images",
                        "frames": [],
                        "roi": None,
                        "roi_params": roi_params,
                    },
                    indent=2,
                ),
            )
        return buf.getvalue()

    roi = compute_motion_roi([im for _, im in pil], **{k: v for k, v in roi_params.items()})
    buf = BytesIO()

    frames_meta = []
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, img in pil:
            if roi is None:
                continue
            prev = build_roi_preview(filename=name, image=img, roi=roi)
            frames_meta.append({"filename": name, "roi": list(roi)})

            overlay_bytes = BytesIO()
            Image.open(BytesIO(base64_to_bytes(prev.overlay_png_base64))).save(overlay_bytes, format="PNG")

        # The above is wasteful; write directly from rendered images instead.

    # Re-open zip to write efficiently (avoid decode/encode churn)
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        if roi is not None:
            from PIL import ImageDraw

            x0, y0, x1, y1 = roi
            for name, img in pil:
                overlay = img.copy()
                draw = ImageDraw.Draw(overlay)
                for w in (0, 1, 2):
                    draw.rectangle([x0 - w, y0 - w, x1 + w, y1 + w], outline=(255, 0, 0))
                crop = img.crop((x0, y0, x1, y1))

                ob = BytesIO(); overlay.save(ob, format="PNG"); ob.seek(0)
                cb = BytesIO(); crop.save(cb, format="PNG"); cb.seek(0)

                # Sanitize filename for zip paths.
                # Also strip the original extension so we don't end up with *.jpg.png
                safe = name.replace("/", "_").replace("\\", "_")
                base = safe.rsplit(".", 1)[0] if "." in safe else safe
                z.writestr(f"overlay/{base}.png", ob.getvalue())
                z.writestr(f"crop/{base}.png", cb.getvalue())
                frames_meta.append({"filename": name, "safe": base, "roi": [x0, y0, x1, y1]})

        z.writestr(
            "manifest.json",
            json.dumps(
                {
                    "roi": list(roi) if roi is not None else None,
                    "roi_params": roi_params,
                    "frames": frames_meta,
                    "how_to_label": {
                        "suggested": "Create a copy of manifest.json and add label fields per frame: good/bad + note",
                        "fields": {"label": "good|bad", "note": "free text"},
                    },
                },
                indent=2,
            ),
        )

    return buf.getvalue()


def base64_to_bytes(s: str) -> bytes:
    import base64

    return base64.b64decode(s.encode("ascii"))
