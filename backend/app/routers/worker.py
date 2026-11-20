# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The REANNZ cluster's side door: pending work, leases, heartbeats and results parts.

Called only by ``cluster/reannz/ww_cron.sh`` and ``app.jobs.batch_infer`` with the
environment's lease token as a bearer token. The API keeps only its SHA-256
(``WORKER_TOKEN_SHA256``); with that unset every route answers 404, so the router is
inert by default. Not in the OpenAPI document: this is not a public API.
Contract and flow: ``domain/batch_lease.py`` and the migration report.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.config import settings
from app.domain import batch_lease

# The largest results part the API accepts on the azure and local backends.
MAX_PART_BYTES = 50 * 1024 * 1024


def require_worker_token(authorization: Optional[str] = Header(None)) -> None:
    """The bearer token's SHA-256 must equal ``WORKER_TOKEN_SHA256``; 404 while that is unset."""
    expected = settings.WORKER_TOKEN_SHA256.strip().lower()
    if not expected:
        raise HTTPException(status_code=404, detail="Not Found")
    token = (authorization or "")[7:].strip() if (authorization or "").lower().startswith("bearer ") else ""
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if not token or not hmac.compare_digest(digest, expected):
        raise HTTPException(status_code=401, detail="Invalid worker token")


router = APIRouter(prefix="/api/worker", tags=["worker"], include_in_schema=False, dependencies=[Depends(require_worker_token)])


class LeaseRequest(BaseModel):
    max_photos: Optional[int] = Field(None, ge=1)
    slurm_job_id: str = ""


class HeartbeatRequest(BaseModel):
    done: int = Field(0, ge=0)
    total: int = Field(0, ge=0)


class PartRequest(BaseModel):
    job_id: str
    part: int = Field(..., ge=1)


class JobReport(BaseModel):
    job_id: str
    parts_total: int = Field(0, ge=0)
    failed_media: list[str] = Field(default_factory=list)


class DoneRequest(BaseModel):
    jobs: list[JobReport]


def _lease_error(exc: batch_lease.LeaseError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


@router.get("/pending")
async def pending():
    """Whether the cron should submit a Slurm job now, the image to run and its ``--time``."""
    return await batch_lease.pending_summary()


@router.post("/lease")
async def lease(body: LeaseRequest):
    """Take queued work. ``{"lease_id": null}`` when there is none."""
    manifest = await batch_lease.claim(body.max_photos, body.slurm_job_id)
    return manifest or {"lease_id": None}


@router.post("/lease/{lease_id}/heartbeat")
async def heartbeat(lease_id: str, body: HeartbeatRequest):
    return await batch_lease.heartbeat(lease_id, body.done, body.total)


@router.post("/lease/{lease_id}/part")
async def part_target(lease_id: str, body: PartRequest):
    """Where to PUT results part ``part`` of ``job_id``."""
    try:
        return await batch_lease.part_target(lease_id, body.job_id, body.part)
    except batch_lease.LeaseError as exc:
        raise _lease_error(exc) from exc


@router.put("/lease/{lease_id}/part/{job_id}/{part}")
async def store_part(lease_id: str, job_id: str, part: int, request: Request):
    """Receive a part when the object store is not S3 (azure, local)."""
    data = await request.body()
    if not data or len(data) > MAX_PART_BYTES:
        raise HTTPException(status_code=413, detail="Part empty or too large")
    try:
        await batch_lease.store_part(lease_id, job_id, part, data)
    except batch_lease.LeaseError as exc:
        raise _lease_error(exc) from exc
    return {"stored": True}


@router.post("/lease/{lease_id}/part/complete")
async def part_complete(lease_id: str, body: PartRequest):
    """The part is uploaded: ingest it."""
    try:
        await batch_lease.part_complete(lease_id, body.job_id, body.part)
    except batch_lease.LeaseError as exc:
        raise _lease_error(exc) from exc
    return {"ingesting": True}


@router.post("/lease/{lease_id}/done")
async def done(lease_id: str, body: DoneRequest):
    """The cluster is finished with the lease: part counts per job, and the photos it could not read."""
    try:
        await batch_lease.finish(lease_id, [r.model_dump() for r in body.jobs])
    except batch_lease.LeaseError as exc:
        raise _lease_error(exc) from exc
    return {"released": True}


@router.get("/lease/{lease_id}/media/{media_id}")
async def media(lease_id: str, media_id: str, crop: bool = False):
    """One leased original or hero crop, for originals that are not in S3."""
    try:
        found = await batch_lease.media_bytes(lease_id, media_id, crop=crop)
    except batch_lease.LeaseError as exc:
        raise _lease_error(exc) from exc
    if not found:
        raise HTTPException(status_code=404, detail="Media not resolvable")
    data, content_type = found
    return Response(content=data, media_type=content_type)
