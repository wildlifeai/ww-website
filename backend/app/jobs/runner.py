# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Lightweight local job runner.

Replaces ARQ and Redis for single-container architectures. Uses asyncio
background tasks and maintains strong references to prevent garbage collection.
"""

import asyncio
from typing import Any, Coroutine, Set

import structlog

logger = structlog.get_logger()

# Store strong references to background tasks to prevent mid-flight GC
_background_tasks: Set[asyncio.Task] = set()


def enqueue_local_job(coro: Coroutine[Any, Any, Any]) -> None:
    """Run an async job in the background within the current event loop.

    Args:
        coro: The coroutine to execute.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.error("no_running_event_loop_for_job")
        return

    task = loop.create_task(coro)
    _background_tasks.add(task)

    def _on_completion(t: asyncio.Task):
        _background_tasks.discard(t)
        try:
            exc = t.exception()
            if exc:
                logger.error("background_job_failed", error=str(exc))
        except asyncio.CancelledError:
            logger.warning("background_job_cancelled")

    task.add_done_callback(_on_completion)
    logger.debug("background_job_enqueued", active_tasks=len(_background_tasks))
