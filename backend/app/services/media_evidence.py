# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Writer for the ``media_evidence`` table (infrastructure layer, Supabase).

The table is owned by ww-backend and does not exist yet (2026-09-29). Its
contract is section 8 of the evidence-pipeline architecture report, used
verbatim here::

    media_evidence(id, media_id, deployment_id, signal text, value float4,
                   value_text text, source text, source_version text,
                   computed_at timestamptz, run_id uuid)
    unique (media_id, signal, source, source_version)

``source`` is the producer (``SOURCES``), ``source_version`` the model id or
code version. A numeric signal goes in ``value``, a label in ``value_text``,
and a signal may carry both (``gemini_visibility``: 0.66 and ``partial``).

Until the migration lands the writer must not break the pipeline: it probes
once per process whether the table exists (a select limited to zero rows
through the service client), logs ``media_evidence_table_missing`` once, and
then every :func:`write_signals` call logs ``media_evidence_write_skipped``
(count and deployment) and returns 0 until the process restarts.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

import structlog

logger = structlog.get_logger()

SIGNALS = (
    "speciesnet_presence",
    "speciesnet_max_conf",
    "gemini_presence",
    "gemini_visibility",
    "gemini_size",
    "motion_frac",
    "edge_presence",
    "edge_score",
    "burst_id",
    "burst_index",
    "burst_len",
    "burst_animal_count",
    "neighbour_animal",
    "evidence_score",
    "evidence_threshold",
    "evidence_weights_version",
)
SOURCES = ("speciesnet", "gemini", "edge", "motion", "bursts", "fusion")

UPSERT_CONFLICT = "media_id,signal,source,source_version"
_CHUNK = 200

# Substrings of the PostgREST / Postgres error for a missing relation
# (42P01 "relation ... does not exist"; PGRST205 "Could not find the table").
_MISSING_MARKERS = ("does not exist", "could not find the table", "42p01", "pgrst205")

# None = not probed yet; True/False = the probe's answer, kept for the process lifetime.
_table_present: Optional[bool] = None


def reset_probe() -> None:
    """Forget the probe result (tests, or after a migration in a long-lived process)."""
    global _table_present
    _table_present = None


def _is_missing_relation(exc: BaseException) -> bool:
    text = str(exc).lower()
    code = str(getattr(exc, "code", "") or "").lower()
    return any(m in text for m in _MISSING_MARKERS) or code in ("42p01", "pgrst205")


def table_available(svc) -> bool:
    """Whether ``media_evidence`` exists, probed once per process.

    A "relation does not exist" answer is cached as False (logged once). Any
    other failure (network, auth) is logged and returns False WITHOUT caching, so
    a transient error does not silence the writer for the rest of the process.
    """
    global _table_present
    if _table_present is not None:
        return _table_present
    try:
        svc.table("media_evidence").select("id").limit(0).execute()
    except Exception as exc:
        if _is_missing_relation(exc):
            _table_present = False
            logger.warning("media_evidence_table_missing", hint="ww-backend migration pending; evidence signals are not persisted")
            return False
        logger.warning("media_evidence_probe_failed", error=str(exc))
        return False
    _table_present = True
    return True


def signal_rows(
    media_id: str,
    deployment_id: str,
    signals: dict[str, Any],
    *,
    source: str,
    source_version: str,
    computed_at: str,
    run_id: Optional[str],
) -> list[dict]:
    """Rows in the table's shape for one media's signals from one source (pure).

    ``signals`` maps a name from ``SIGNALS`` to a number (``value``), a string
    (``value_text``), a ``(number, string)`` pair (both), or None (absent: no
    row, never 0). An unknown signal or source raises so a typo never lands in
    the table.
    """
    if source not in SOURCES:
        raise ValueError(f"unknown media_evidence source {source!r}")
    rows: list[dict] = []
    for name, raw in signals.items():
        if name not in SIGNALS:
            raise ValueError(f"unknown media_evidence signal {name!r}")
        if raw is None:
            continue
        value: Optional[float] = None
        value_text: Optional[str] = None
        if isinstance(raw, tuple):
            value = None if raw[0] is None else float(raw[0])
            value_text = None if raw[1] is None else str(raw[1])
        elif isinstance(raw, bool):
            value = 1.0 if raw else 0.0
        elif isinstance(raw, (int, float)):
            value = float(raw)
        else:
            value_text = str(raw)
        if value is None and value_text is None:
            continue
        rows.append(
            {
                "media_id": media_id,
                "deployment_id": deployment_id,
                "signal": name,
                "value": value,
                "value_text": value_text,
                "source": source,
                "source_version": source_version,
                "computed_at": computed_at,
                "run_id": run_id,
            }
        )
    return rows


def write_signals(svc, rows: Iterable[dict], deployment_id: Optional[str] = None) -> int:
    """Upsert ``rows`` (from :func:`signal_rows`) on the table's unique key; returns rows written.

    Returns 0 without touching the database when the table is missing, logging
    ``media_evidence_write_skipped`` with the count so nothing is silently lost.
    """
    batch = list(rows)
    if not batch:
        return 0
    if not table_available(svc):
        logger.info("media_evidence_write_skipped", count=len(batch), deployment_id=deployment_id)
        return 0
    written = 0
    for i in range(0, len(batch), _CHUNK):
        chunk = batch[i : i + _CHUNK]
        svc.table("media_evidence").upsert(chunk, on_conflict=UPSERT_CONFLICT).execute()
        written += len(chunk)
    return written


def read_signal(svc, media_ids: list[str], signal: str, source: Optional[str] = None) -> dict[str, float]:
    """``{media_id: value}`` for one numeric signal over ``media_ids``; empty when the table is missing.

    When several source versions wrote the signal the latest ``computed_at`` wins.
    """
    if not media_ids or not table_available(svc):
        return {}
    out: dict[str, tuple[str, float]] = {}
    for i in range(0, len(media_ids), 100):
        q = svc.table("media_evidence").select("media_id, value, computed_at").in_("media_id", media_ids[i : i + 100]).eq("signal", signal)
        if source:
            q = q.eq("source", source)
        for r in q.execute().data or []:
            if r.get("value") is None:
                continue
            stamp = str(r.get("computed_at") or "")
            if r["media_id"] not in out or stamp > out[r["media_id"]][0]:
                out[r["media_id"]] = (stamp, float(r["value"]))
    return {k: v for k, (_, v) in out.items()}
