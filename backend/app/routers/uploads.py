# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Direct-to-S3 uploads: the browser asks for presigned POSTs, then sends the endpoint keys.

GET /api/uploads/config says how this server takes files: ``s3`` (presign, then send
``<field>_key`` form fields) or ``multipart`` (today's request bodies). POST
/api/uploads/presign returns one presigned POST per file under ``incoming/<user>/<upload>/``,
refused by S3 above the purpose's size cap. ``services/incoming.py`` reads the keys back.
"""

from __future__ import annotations

import re
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.dependencies import get_current_user, require_not_demo
from app.services import object_store
from app.services.incoming import incoming_prefix

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

PRESIGN_TTL_SECONDS = 900
MAX_FILES_PER_PRESIGN = 500
Purpose = Literal["photos", "camtrapdp", "model"]


def size_cap(purpose: str) -> int:
    """The largest file each upload endpoint takes, in bytes."""
    if purpose == "photos":
        return settings.GOOGLE_DRIVE_MAX_FILE_SIZE_MB * 1024 * 1024
    return 50 * 1024 * 1024  # CamtrapDP packages (routers/camtrapdp.py) and model files


def safe_name(name: str) -> str:
    """A file name safe as the last segment of a key (the endpoint reads it back as the filename)."""
    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "_", name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]).strip(" .")
    return cleaned[:120] or "file"


class PresignFile(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    size: int = Field(..., ge=1)
    content_type: str = Field("application/octet-stream", max_length=127)


class PresignRequest(BaseModel):
    purpose: Purpose
    files: list[PresignFile] = Field(..., min_length=1, max_length=MAX_FILES_PER_PRESIGN)


@router.get("/config")
async def upload_config(user=Depends(get_current_user)):
    """How this server takes uploaded files."""
    mode = "s3" if settings.OBJECT_STORE_BACKEND == "s3" else "multipart"
    return {"mode": mode, "max_bytes": {p: size_cap(p) for p in ("photos", "camtrapdp", "model")}}


@router.post("/presign", dependencies=[Depends(require_not_demo)])
async def presign(body: PresignRequest, user=Depends(get_current_user)):
    """One presigned POST per file; send each file to its ``url`` with its ``fields``, then the ``key`` to the endpoint."""
    if settings.OBJECT_STORE_BACKEND != "s3":
        raise HTTPException(status_code=404, detail="This server takes files in the request body.")
    cap = size_cap(body.purpose)
    too_big = [f.name for f in body.files if f.size > cap]
    if too_big:
        raise HTTPException(status_code=413, detail=f"Over {cap // (1024 * 1024)} MB: {', '.join(too_big[:5])}")
    upload_id = str(uuid.uuid4())
    out = []
    for n, f in enumerate(body.files):
        key = f"{incoming_prefix(user.id)}{upload_id}/{n}-{safe_name(f.name)}"
        post = object_store.presign_post(key, cap, PRESIGN_TTL_SECONDS, f.content_type)
        out.append({"key": key, "url": post["url"], "fields": post["fields"]})
    return {"upload_id": upload_id, "files": out}
