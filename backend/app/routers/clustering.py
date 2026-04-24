# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Image clustering router — near-duplicate detection for camera trap images.

Provides an endpoint to upload images and receive cluster assignments
with representative selections, ready for iNaturalist upload.
"""

import csv
import io
from typing import List

from fastapi import APIRouter, File, Form, UploadFile, Request
from fastapi.responses import StreamingResponse

from app.config import settings
from app.schemas.common import ApiResponse, ApiMeta
from app.domain.clustering import cluster_images_from_bytes

import structlog

logger = structlog.get_logger()

router = APIRouter(prefix="/api/clustering", tags=["clustering"])

MAX_CLUSTERING_IMAGES = 1000  # Safety limit


@router.post("/analyze")
async def analyze_images(
    request: Request,
    files: List[UploadFile] = File(...),
    max_hamming: int = Form(10),
):
    """Cluster uploaded images by visual similarity.

    Accepts uploaded JPEG/PNG images and returns cluster assignments
    with representative selections (sharpest image per cluster).

    Parameters
    ----------
    files : List[UploadFile]
        Images to cluster (JPEG, PNG, WebP).
    max_hamming : int, optional
        Similarity threshold (0-20). Lower = stricter. Default 10.

    Returns
    -------
    Cluster summary with assignments and representative filenames.
    """
    if len(files) > MAX_CLUSTERING_IMAGES:
        return ApiResponse(
            data=None,
            error={
                "code": "TOO_MANY_IMAGES",
                "message": f"Maximum {MAX_CLUSTERING_IMAGES} images per request.",
            },
            meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
        )

    # Read all files into memory
    image_data = []
    for upload in files:
        content = await upload.read()
        if content:
            image_data.append((upload.filename or "unknown.jpg", content))

    if not image_data:
        return ApiResponse(
            data={"total_images": 0, "total_clusters": 0, "clusters": []},
            meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
        )

    # Run clustering
    result = cluster_images_from_bytes(
        files=image_data,
        max_hamming=max(0, min(20, max_hamming)),
    )

    # Build response
    # Dense cluster IDs for readability
    roots = list(result.clusters.keys())
    root_to_cid = {r: i for i, r in enumerate(roots)}

    clusters_summary = []
    for root, member_idxs in result.clusters.items():
        rep_idx = result.representatives.get(root)
        rep_filename = result.records[rep_idx].filename if rep_idx is not None else None

        members = []
        for idx in member_idxs:
            rec = result.records[idx]
            members.append({
                "filename": rec.filename,
                "sharpness": round(rec.sharpness, 2),
                "width": rec.width,
                "height": rec.height,
                "is_representative": idx == rep_idx,
            })

        clusters_summary.append({
            "cluster_id": root_to_cid[root],
            "size": len(member_idxs),
            "representative": rep_filename,
            "members": members,
        })

    return ApiResponse(
        data={
            "total_images": result.total_images,
            "total_clusters": result.total_clusters,
            "total_representatives": result.total_representatives,
            "clusters": clusters_summary,
        },
        meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
    )


@router.post("/analyze/csv")
async def analyze_images_csv(
    request: Request,
    files: List[UploadFile] = File(...),
    max_hamming: int = Form(10),
):
    """Cluster images and return results as a downloadable CSV.

    Same logic as /analyze but returns a CSV file with one row per image.
    """
    if len(files) > MAX_CLUSTERING_IMAGES:
        return ApiResponse(
            data=None,
            error={
                "code": "TOO_MANY_IMAGES",
                "message": f"Maximum {MAX_CLUSTERING_IMAGES} images per request.",
            },
            meta=ApiMeta(request_id=getattr(request.state, "request_id", None)),
        )

    image_data = []
    for upload in files:
        content = await upload.read()
        if content:
            image_data.append((upload.filename or "unknown.jpg", content))

    if not image_data:
        output = io.StringIO()
        output.write("filename,cluster_id,cluster_size,is_representative,sharpness,width,height\n")
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=clustering.csv"},
        )

    result = cluster_images_from_bytes(
        files=image_data,
        max_hamming=max(0, min(20, max_hamming)),
    )

    # Build CSV
    roots = list(result.clusters.keys())
    root_to_cid = {r: i for i, r in enumerate(roots)}

    # Index -> root lookup
    idx_to_root = {}
    for root, idxs in result.clusters.items():
        for idx in idxs:
            idx_to_root[idx] = root

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "filename", "cluster_id", "cluster_size",
            "is_representative", "sharpness", "width", "height",
        ],
    )
    writer.writeheader()

    for idx, rec in enumerate(result.records):
        root = idx_to_root.get(idx)
        if root is None:
            continue
        writer.writerow({
            "filename": rec.filename,
            "cluster_id": root_to_cid[root],
            "cluster_size": len(result.clusters[root]),
            "is_representative": 1 if result.representatives.get(root) == idx else 0,
            "sharpness": f"{rec.sharpness:.4f}",
            "width": rec.width,
            "height": rec.height,
        })

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=clustering.csv"},
    )
