# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Standard API response envelope.

Every endpoint returns either:
  { "data": ..., "meta": { "request_id": "..." } }
or:
  { "error": { "code": "...", "message": "...", "retryable": bool }, "meta": { ... } }
"""

from pydantic import BaseModel, Field
from typing import Any, Optional


class ApiError(BaseModel):
    """Structured error payload."""

    code: str = Field(..., description="Machine-readable error code")
    message: str = Field(..., description="Human-readable error message")
    retryable: bool = Field(False, description="Whether the client should retry")
    details: Optional[str] = Field(None, description="Extra diagnostic info")


class ApiMeta(BaseModel):
    """Response metadata."""

    request_id: Optional[str] = None
    total: Optional[int] = None
    page: Optional[int] = None


class ApiResponse(BaseModel):
    """Standard response envelope wrapping all API responses."""

    data: Optional[Any] = None
    error: Optional[ApiError] = None
    meta: ApiMeta = Field(default_factory=ApiMeta)
