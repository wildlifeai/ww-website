"""Reflect on-device (edge) model predictions as observations — lifecycle stage 8.

The camera writes per-class NN scores into each JPEG's EXIF UserComment
(``"rat: 87%; not rat: 13%; "``); SD-card ingest parses them into
``media.exif_metadata->user_comment_fields`` (see ``domain/exif.py``). This module
turns those fields into ``observations`` rows tagged ``ai_origin='edge'`` — the
Camera AI result shown beside the Cloud AI (SpeciesNet) result — using the project
model's ``ai_models.label_map`` to say what each label asserts and to skip
background classes (e.g. ``not rat``). A target class predicts a taxon (an
``animal`` row with the class's taxon) or a type (``person`` → a ``human`` row
with no taxon); the rules are LM-10 in ``domain/label_map.py``.

Gated on ``FF_EDGE_REFLECT_ENABLED``: requires the ``observations.ai_origin``
column (ww-backend migration ``dual_ai_v0``). Best-effort by design — a
deployment with no project model, an unmapped label_map, or media without
UserComment scores simply yields no edge rows.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import structlog

from app.domain.label_map import target_observation_fields
from app.services.db_utils import rows_of
from app.services.supabase_client import create_service_client

logger = structlog.get_logger()

# A label whose label_map entry carries no explicit threshold is reflected only
# above this confidence — keeps sub-majority scores from becoming taxon claims.
DEFAULT_REFLECT_THRESHOLD_PCT = 50

_MEDIA_PAGE = 1000


def _parse_pct(value) -> int | None:
    """``"87%"`` / ``"87"`` / ``87`` → ``87``; non-numeric → ``None``."""
    try:
        return int(round(float(str(value).strip().rstrip("%"))))
    except (TypeError, ValueError):
        return None


def edge_model_version(model: dict) -> str | None:
    """The device-facing identity ``{firmware_model_id}V{version_number}``.

    Matches the ``.TFL`` filename the manifest deploys, closing the provenance
    loop from device file → EXIF → observation row.
    """
    family = model.get("ai_model_families") or {}
    fw_id = family.get("firmware_model_id")
    ver = model.get("version_number")
    if fw_id is None or ver is None:
        return None
    return f"{fw_id}V{ver}"


def build_edge_observations(
    media_row: dict,
    deployment_id: str,
    model: dict,
    timestamp: str,
) -> list[dict]:
    """Map one media row's UserComment scores to edge observation rows (pure).

    One row per *target* label at/above its threshold (label_map ``threshold``,
    else ``DEFAULT_REFLECT_THRESHOLD_PCT``), typed by what the class predicts
    (``label_map.target_observation_fields``). Background/negative classes,
    classes LM-10 rejects (behaviour, a blank type, a target with no value) and
    non-numeric UserComment fields (device telemetry like ``Batt: 87``) are
    skipped. No bbox — the on-device classifier is whole-image.
    """
    exif = media_row.get("exif_metadata") or {}
    fields = exif.get("user_comment_fields") or {}
    label_map = model.get("label_map") or {}
    version = edge_model_version(model)
    # media_row["id"] becomes observations.media_id (a required FK) below, so a
    # row without one can't yield a valid observation — skip it.
    if not media_row.get("id") or not fields or not label_map or not version:
        return []

    rows: list[dict] = []
    for label, raw_value in fields.items():
        entry = label_map.get(label)
        if not entry:
            continue
        observed = target_observation_fields(entry)
        if observed is None:
            continue
        pct = _parse_pct(raw_value)
        if pct is None:
            continue
        threshold = entry.get("threshold") or DEFAULT_REFLECT_THRESHOLD_PCT
        if pct < threshold:
            continue
        rows.append(
            {
                "id": str(uuid.uuid4()),
                "deployment_id": deployment_id,
                "media_id": media_row["id"],
                "observation_level": "media",
                **observed,
                "source_type": "ai",
                "ai_origin": "edge",
                "source_model_id": model.get("id"),
                "source_model_version": version,
                "review_status": "ai_reviewed",
                "classification_method": "machine",
                "classified_by": version,
                "classification_timestamp": timestamp,
                "classification_probability": pct / 100.0,
            }
        )
    return rows


def _project_model_for_deployment(svc, deployment_id: str) -> dict | None:
    """The deployment's project model (with label_map + firmware identity), or None."""
    res = (
        svc.table("deployments")
        .select("id, projects(model_id, ai_models(id, name, version_number, label_map, ai_model_families(firmware_model_id)))")
        .eq("id", deployment_id)
        .limit(1)
        .execute()
    )
    row = (res.data or [None])[0] or {}
    project = row.get("projects") or {}
    return project.get("ai_models") or None


async def reflect_edge_deployment(deployment_id: str) -> int:
    """Create edge observations for a deployment's media from their EXIF scores.

    Idempotent: prior ``ai_reviewed`` rows for this edge model version are
    replaced (same replace-don't-append contract as the SpeciesNet step —
    human-reviewed rows are never touched). Returns rows created; 0 when the
    flag is off or there is nothing to reflect.
    """
    from app.config import settings

    if not settings.FF_EDGE_REFLECT_ENABLED:
        return 0

    from app.domain.pipeline import delete_superseded_ai_observations

    def _run() -> int:
        svc = create_service_client()
        model = _project_model_for_deployment(svc, deployment_id)
        if not model or not (model.get("label_map") or {}):
            logger.info("edge_reflect_skipped", deployment_id=deployment_id, reason="no project model or empty label_map")
            return 0
        version = edge_model_version(model)
        if not version:
            logger.warning("edge_reflect_skipped", deployment_id=deployment_id, reason="model missing firmware identity")
            return 0

        timestamp = datetime.now(timezone.utc).isoformat()
        rows: list[dict] = []
        reflected_media: set[str] = set()
        offset = 0
        while True:
            page = rows_of(
                svc.table("media").select("id, exif_metadata").eq("deployment_id", deployment_id).range(offset, offset + _MEDIA_PAGE - 1).execute()
            )
            for m in page:
                media_rows = build_edge_observations(m, deployment_id, model, timestamp)
                if media_rows:
                    rows.extend(media_rows)
                    reflected_media.add(m["id"])
            if len(page) < _MEDIA_PAGE:
                break
            offset += _MEDIA_PAGE

        if not rows:
            return 0
        delete_superseded_ai_observations(svc, reflected_media, version)
        for i in range(0, len(rows), 50):
            svc.table("observations").insert(rows[i : i + 50]).execute()
        return len(rows)

    try:
        created = await asyncio.to_thread(_run)
        if created:
            logger.info("edge_reflect_complete", deployment_id=deployment_id, observations_created=created)
        return created
    except Exception as exc:  # best-effort: edge reflection never breaks the upload flow
        logger.warning("edge_reflect_failed", deployment_id=deployment_id, error=str(exc))
        return 0
