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

from dataclasses import dataclass, field
from io import BytesIO
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    from PIL import Image
except ImportError as e:  # pragma: no cover
    raise RuntimeError("Pillow is required for clustering.") from e

import structlog

logger = structlog.get_logger()

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
    index: int = 0  # position in the original input list


@dataclass
class ClusterResult:
    """Result of the clustering pipeline."""

    records: List[ImageRecord]
    clusters: Dict[int, List[int]]  # root_idx -> [member indices]
    representatives: Dict[int, int]  # root_idx -> representative idx
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
    records = []
    for i, (filename, image_bytes) in enumerate(files):
        rec = build_record_from_bytes(filename, image_bytes, i, hash_size)
        if rec:
            records.append(rec)

    clusters = cluster_by_dhash(records, max_hamming=max_hamming)
    reps = pick_representatives(records, clusters)

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
        total_images=len(records),
        total_clusters=len(clusters),
        total_representatives=len(reps),
    )
