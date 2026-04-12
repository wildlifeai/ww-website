#!/usr/bin/env python3
# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later

"""Cluster images and select representatives (Stage 2A).

This is a local, testable step that does *not* require iNaturalist credentials.

Usage:
  python3 scripts/cluster_images.py --input /path/to/images --out clusters.csv

The output CSV includes one row per image with cluster assignment and whether
it was selected as the representative.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import sys

# Allow running from repo root: add project root (parent of scripts/) to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from cluster_utils import build_cluster_manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Folder (or single image) to cluster")
    ap.add_argument("--out", default="clusters.csv", help="Output CSV path")
    ap.add_argument("--max-hamming", type=int, default=10, help="dHash Hamming threshold")
    ap.add_argument("--hash-size", type=int, default=8, help="dHash size (8 => 64-bit)")
    args = ap.parse_args()

    root = Path(args.input).expanduser().resolve()
    if not root.exists():
        raise SystemExit(f"Input not found: {root}")

    records, clusters, reps = build_cluster_manifest(root, max_hamming=args.max_hamming, hash_size=args.hash_size)

    # Map index -> cluster_id (dense ids for readability)
    roots = list(clusters.keys())
    root_to_cluster_id = {r: i for i, r in enumerate(roots)}

    # Precompute idx -> root
    idx_to_root = {}
    for r, idxs in clusters.items():
        for i in idxs:
            idx_to_root[i] = r

    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "path",
                "cluster_id",
                "cluster_size",
                "is_representative",
                "sharpness",
                "width",
                "height",
            ],
        )
        w.writeheader()

        for idx, rec in enumerate(records):
            r = idx_to_root.get(idx)
            if r is None:
                continue
            cid = root_to_cluster_id[r]
            size = len(clusters[r])
            w.writerow(
                {
                    "path": str(rec.path),
                    "cluster_id": cid,
                    "cluster_size": size,
                    "is_representative": "1" if reps.get(r) == idx else "0",
                    "sharpness": f"{rec.sharpness:.4f}",
                    "width": rec.width,
                    "height": rec.height,
                }
            )

    reps_count = len(reps)
    print(f"Images: {len(records)}")
    print(f"Clusters: {len(clusters)}")
    print(f"Representatives: {reps_count}")
    print(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
