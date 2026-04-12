# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later

"""Stage 2A: lightweight image clustering and representative selection.

Design goals:
- Keep dependencies small and CPU-friendly for Streamlit Cloud.
- Provide a deterministic, testable pipeline (CLI + Streamlit can share).
- Do *not* depend on iNaturalist credentials.

Approach (MVP):
- Perceptual hash (dHash) embedding for each image
- Cluster with a simple union-find based on Hamming distance threshold
- Pick a representative per cluster (sharpest image by Laplacian variance)

This is intentionally not a foundation-model embedding yet; it’s a reliable
first step to dedupe near-identical camtrap frames and burst sequences.

We can upgrade later to transformer embeddings + HDBSCAN when you’re ready.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
from datetime import datetime

try:
    from PIL import Image
except Exception as e:  # pragma: no cover
    raise RuntimeError("Pillow is required for clustering utilities.") from e


SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


@dataclass
class ImageRecord:
    path: Path
    dhash: int
    sharpness: float
    width: int
    height: int


def iter_image_paths(root: Path) -> List[Path]:
    if root.is_file():
        return [root]

    paths: List[Path] = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
            paths.append(p)
    paths.sort()
    return paths


def group_paths_by_mtime(paths: List[Path], max_gap_seconds: int = 20) -> List[List[Path]]:
    """Group image paths into "events" by filesystem modified time.

    This is a fallback when EXIF timestamps aren’t available.
    For camtrap exports, mtime often preserves capture ordering.
    """

    if not paths:
        return []

    items = []
    for p in paths:
        try:
            ts = p.stat().st_mtime
        except Exception:
            ts = None
        items.append((p, ts))

    # Sort by timestamp when available, else by name
    items.sort(key=lambda x: (x[1] is None, x[1] if x[1] is not None else 0, x[0].name))

    groups: List[List[Path]] = []
    cur: List[Path] = [items[0][0]]
    prev_ts = items[0][1]

    for p, ts in items[1:]:
        if prev_ts is None or ts is None:
            # If timestamps are missing, just append to the current group
            cur.append(p)
            prev_ts = ts
            continue

        if ts - prev_ts <= max_gap_seconds:
            cur.append(p)
        else:
            groups.append(cur)
            cur = [p]
        prev_ts = ts

    groups.append(cur)
    return groups


def _to_grayscale_small(img: Image.Image, size: Tuple[int, int]) -> np.ndarray:
    return np.asarray(img.convert("L").resize(size, Image.BILINEAR), dtype=np.uint8)


def compute_dhash(img: Image.Image, hash_size: int = 8) -> int:
    """Difference hash (dHash), returns 64-bit int when hash_size=8."""
    # dHash uses (hash_size+1, hash_size) so we can compare adjacent pixels horizontally
    gray = _to_grayscale_small(img, (hash_size + 1, hash_size))
    diff = gray[:, 1:] > gray[:, :-1]

    # Pack bits row-major into an int
    bits = diff.flatten().astype(np.uint8)
    out = 0
    for b in bits:
        out = (out << 1) | int(b)
    return out


def hamming_distance64(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def laplacian_variance_sharpness(img: Image.Image) -> float:
    """Simple sharpness metric; higher = sharper.

    Uses a tiny Laplacian kernel implemented with numpy (no OpenCV dependency).
    """
    g = np.asarray(img.convert("L"), dtype=np.float32)
    if g.size == 0:
        return 0.0

    # 3x3 Laplacian kernel
    k = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float32)

    # Convolution (valid region)
    if g.shape[0] < 3 or g.shape[1] < 3:
        return float(np.var(g))

    sub = (
        k[0, 1] * g[:-2, 1:-1]
        + k[1, 0] * g[1:-1, :-2]
        + k[1, 1] * g[1:-1, 1:-1]
        + k[1, 2] * g[1:-1, 2:]
        + k[2, 1] * g[2:, 1:-1]
    )
    return float(np.var(sub))


def build_records(paths: Iterable[Path], hash_size: int = 8) -> List[ImageRecord]:
    records: List[ImageRecord] = []
    for p in paths:
        try:
            with Image.open(p) as img:
                img = img.copy()  # close file handle early
            dh = compute_dhash(img, hash_size=hash_size)
            sharp = laplacian_variance_sharpness(img)
            records.append(ImageRecord(path=p, dhash=dh, sharpness=sharp, width=img.width, height=img.height))
        except Exception:
            # skip unreadable files
            continue

    return records


class _UnionFind:
    def __init__(self, n: int):
        self.p = list(range(n))
        self.r = [0] * n

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
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


def cluster_by_dhash(records: List[ImageRecord], max_hamming: int = 10) -> Dict[int, List[int]]:
    """Cluster indices by dHash within a Hamming threshold.

    This is O(N^2) and intended for small/medium batches.

    For big deployments, we’ll add event grouping first and/or a BK-tree.
    """

    n = len(records)
    uf = _UnionFind(n)

    for i in range(n):
        hi = records[i].dhash
        for j in range(i + 1, n):
            if hamming_distance64(hi, records[j].dhash) <= max_hamming:
                uf.union(i, j)

    clusters: Dict[int, List[int]] = {}
    for i in range(n):
        r = uf.find(i)
        clusters.setdefault(r, []).append(i)

    # Sort clusters by size desc
    return dict(sorted(clusters.items(), key=lambda kv: (-len(kv[1]), kv[0])))


def cluster_by_dhash_in_events(
    records: List[ImageRecord],
    max_hamming: int,
    events: List[List[int]],
) -> Dict[int, List[int]]:
    """Cluster within each event group, then merge results.

    This avoids comparing all images to all images, and reduces accidental merges
    across distant capture times.
    """

    # Build clusters per event; roots are local to each event so we remap.
    all_clusters: Dict[int, List[int]] = {}
    next_root = 0

    for ev in events:
        if len(ev) == 0:
            continue
        sub = [records[i] for i in ev]
        sub_clusters = cluster_by_dhash(sub, max_hamming=max_hamming)
        # Map local indices back to global
        for _, local_idxs in sub_clusters.items():
            global_idxs = [ev[i] for i in local_idxs]
            all_clusters[next_root] = global_idxs
            next_root += 1

    return dict(sorted(all_clusters.items(), key=lambda kv: (-len(kv[1]), kv[0])))


def pick_representatives(records: List[ImageRecord], clusters: Dict[int, List[int]]) -> Dict[int, int]:
    """Return representative index for each cluster root.

    We pick the sharpest image (Laplacian variance) as the representative.
    """

    reps: Dict[int, int] = {}
    for root, idxs in clusters.items():
        best = max(idxs, key=lambda i: (records[i].sharpness, records[i].width * records[i].height))
        reps[root] = best
    return reps


def build_cluster_manifest(
    root: Path,
    max_hamming: int = 10,
    hash_size: int = 8,
) -> Tuple[List[ImageRecord], Dict[int, List[int]], Dict[int, int]]:
    paths = iter_image_paths(root)
    records = build_records(paths, hash_size=hash_size)

    # Event grouping by mtime (cheap heuristic to reduce comparisons)
    idx_by_path = {r.path: i for i, r in enumerate(records)}
    events_paths = group_paths_by_mtime([r.path for r in records], max_gap_seconds=20)
    events = [[idx_by_path[p] for p in grp if p in idx_by_path] for grp in events_paths]

    clusters = cluster_by_dhash_in_events(records, max_hamming=max_hamming, events=events)
    reps = pick_representatives(records, clusters)
    return records, clusters, reps
