# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Hand a job to the CPU Lambda function (``CPU_FUNCTION_NAME``) as an asynchronous invoke.

Used by ``jobs/dispatch.py`` under ``WORKER_BACKEND=lambda``. The function's own retry
setting and on-failure destination (the SAM template: no retries, a dead-letter queue) decide
what happens to a failed run, as ``max_tries = 1`` did for ARQ; the stale-job reaper fails the
``api_jobs`` row. A payload over :data:`MAX_INLINE_BYTES` travels in the object store's
``buffer/`` and the event carries its key.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Optional

import structlog

from app.config import settings

logger = structlog.get_logger()

# Well under the asynchronous invoke payload limit.
MAX_INLINE_BYTES = 200 * 1024
EVENT_MARKER = "ww-job"

_client: Any = None


def lambda_client() -> Any:
    global _client
    if _client is None:
        import boto3

        _client = boto3.client("lambda", region_name=settings.S3_REGION or None)
    return _client


def set_lambda_client(client: Any) -> None:
    """Replace the client (tests)."""
    global _client
    _client = client


def job_event(name: str, args: Any, kwargs: dict, payload_key: Optional[str] = None) -> dict:
    """The event ``lambda_events`` expects for one job."""
    event: dict = {EVENT_MARKER: name}
    if payload_key:
        event["payload_key"] = payload_key
    else:
        event["args"] = list(args)
        event["kwargs"] = kwargs
    return event


async def invoke_job(name: str, args: Any, kwargs: dict) -> None:
    """Start ``name(*args, **kwargs)`` on the CPU function and return at once."""
    body = json.dumps(job_event(name, args, kwargs), default=str).encode("utf-8")
    if len(body) > MAX_INLINE_BYTES:
        from app.services import object_store

        key = f"payloads/{uuid.uuid4()}.json"
        await object_store.store_blob(key, json.dumps({"args": list(args), "kwargs": kwargs}, default=str).encode("utf-8"))
        body = json.dumps(job_event(name, args, kwargs, payload_key=key)).encode("utf-8")
    await asyncio.to_thread(lambda_client().invoke, FunctionName=settings.CPU_FUNCTION_NAME, InvocationType="Event", Payload=body)
