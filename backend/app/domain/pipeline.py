# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pluggable AI inference pipeline framework — pure domain logic.

Defines the PipelineStep abstract base class and the concrete steps that run in
production: media preparation (thumbnails/previews), the SpeciesNet ensemble
(detection + species classification, including blank-frame handling), and animal
cropping for DINOv3. Steps are composable and run sequentially.

No HTTP or FastAPI imports — this module runs in the domain layer.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import structlog

from app.schemas.pipeline import (
    PipelineRunResult,
    PipelineStepResult,
    PipelineStepType,
)
from app.services.bioclip_service import BIOCLIP_VERSION
from app.services.speciesnet_service import SPECIESNET_VERSION
from app.services.supabase_client import create_service_client

logger = structlog.get_logger()

# Steps that run a model on the GPU (SpeciesNet's detector and classifier, BioCLIP). The others
# run on the CPU or call an API, but on the Cloud Run GPU job their seconds are billed too.
GPU_MODEL_STEPS = frozenset({PipelineStepType.SPECIESNET, PipelineStepType.BIOCLIP})


# ── Cloud-model registry IDs ─────────────────────────────────────────
# annotation_runs.chk_annotation_run_provenance requires every ai_inference run to
# cite a model_id (FK → ai_models). The cloud models below have no uploaded artifact,
# so they are seeded as stable system rows (see ww-backend cloud-model seed). These
# UUIDs MUST match those rows. The run's model_id is the primary model among the steps
# that ran, in this priority order.
CLOUD_MODEL_IDS: dict[PipelineStepType, str] = {
    PipelineStepType.SPECIESNET: "a0000000-0000-4000-8000-000000000001",
    PipelineStepType.BIOCLIP: "a0000000-0000-4000-8000-000000000002",
}


def _resolve_run_model_id(steps: list[PipelineStepType]) -> Optional[str]:
    """Pick the primary model for the annotation_runs provenance row (SpeciesNet > BioCLIP)."""
    for step_type in (PipelineStepType.SPECIESNET, PipelineStepType.BIOCLIP):
        if step_type in steps and step_type in CLOUD_MODEL_IDS:
            return CLOUD_MODEL_IDS[step_type]
    return None


# ── Abstract Pipeline Step ───────────────────────────────────────────


class PipelineStep(ABC):
    """Base class for all pipeline inference steps.

    Each step receives a list of media dicts (id, deployment_id, file_path, etc.)
    and produces observation rows which are inserted into the database.
    """

    step_type: PipelineStepType
    model_version: Optional[str] = None

    @abstractmethod
    async def run(
        self,
        media: list[dict],
        deployment_id: str,
        config: dict[str, Any],
    ) -> PipelineStepResult:
        """Execute the pipeline step on a batch of media.

        Args:
            media: List of media dicts from Supabase (id, file_path, etc.).
            deployment_id: UUID of the deployment being processed.
            config: Step-specific configuration overrides.

        Returns:
            PipelineStepResult summarising what was created/updated.
        """
        ...


# ── Media Preparation Step (thumbnails + previews) ───────────────────


class MediaPreparationStep(PipelineStep):
    """Generate thumbnail + preview renditions into media_assets (Azure CDN).

    Runs before SpeciesNet so the UI grid and (later) crops have CDN URLs and
    never hit Google Drive on the hot path. Creates no observations.
    """

    step_type = PipelineStepType.MEDIA_PREP
    model_version = "media-prep-v1"

    async def run(
        self,
        media: list[dict],
        deployment_id: str,
        config: dict[str, Any],
    ) -> PipelineStepResult:
        from app.domain.media_registry import prepare_media_assets

        start = time.monotonic()
        errors = 0
        for m in media:
            try:
                await prepare_media_assets(m)
            except Exception as exc:
                logger.warning("media_prep_error", media_id=m.get("id"), error=str(exc))
                errors += 1

        return PipelineStepResult(
            step=self.step_type,
            media_processed=len(media),
            errors=errors,
            duration_seconds=round(time.monotonic() - start, 2),
            model_version=self.model_version,
        )


# ── Animal Crop Step (DINOv3 input) ──────────────────────────────────


class AnimalCropStep(PipelineStep):
    """Crop every AI animal detection on each frame.

    Writes one crop per observation to observations.crop_url and points the
    media's hero media_assets.animal_crop_url at the highest-confidence crop.
    Runs after SpeciesNet (needs the detection bboxes it wrote). Creates no
    observations; produces the crop DINOv3 consumes in Phase 5.
    """

    step_type = PipelineStepType.ANIMAL_CROP
    model_version = "animal-crop-v1"

    async def run(
        self,
        media: list[dict],
        deployment_id: str,
        config: dict[str, Any],
    ) -> PipelineStepResult:
        from app.config import settings
        from app.domain.media_registry import generate_motion_roi_crops, generate_observation_crops

        start = time.monotonic()
        errors = 0
        cropped_ids: set[str] = set()
        for m in media:
            try:
                if await generate_observation_crops(m["id"]):
                    cropped_ids.add(m["id"])
            except Exception as exc:
                logger.warning("animal_crop_error", media_id=m.get("id"), error=str(exc))
                errors += 1

        # SpeciesNet-free fallback: for frames with no detection crop, crop the motion ROI
        # across each burst so DINOv3 still gets an animal region (e.g. on the lean dev-cloud
        # image where SpeciesNet can't load). Gated, off by default.
        if settings.FF_MOTION_ROI_FALLBACK_ENABLED and len(cropped_ids) < len(media):
            try:
                await generate_motion_roi_crops(
                    deployment_id,
                    media,
                    skip_media_ids=cropped_ids,
                    burst_gap_seconds=settings.BURST_GAP_SECONDS,
                )
            except Exception as exc:
                logger.warning("motion_roi_fallback_error", deployment_id=deployment_id, error=str(exc))

        return PipelineStepResult(
            step=self.step_type,
            media_processed=len(media),
            errors=errors,
            duration_seconds=round(time.monotonic() - start, 2),
            model_version=self.model_version,
        )


# ── Gemini Presence Step (VLM animal-present verdict) ────────────────


def build_gemini_presence_observation(
    media: dict,
    deployment_id: str,
    verdict,  # services.gemini_presence.PresenceVerdict (duck-typed)
    model: str,
    timestamp: str,
) -> dict:
    """Map one Gemini presence verdict to a CamtrapDP observation row (pure).

    Column names and CHECK constraints verified against ww-backend
    ``supabase/schemas/public/tables/35_observations.sql`` (2026-09-26):
    ``observation_type`` in (animal, human, vehicle, blank, unknown); ``ai_origin``
    in (edge, cloud); ``source_type`` in (ai, human, imported, consensus);
    ``review_status`` in (unreviewed, ai_reviewed, ...); ``classification_method``
    in (human, machine); ``confidence`` 0-1; bbox is a complete quad on 0-1 or
    absent (``chk_bbox_complete``). ``source_model_version`` and ``classified_by``
    carry the Gemini model id, which is how the row is told apart from
    SpeciesNet's and how a re-run finds it (idempotence).

    ``confidence`` is written only for a v1 answer (v2 does not ask for it and it
    is never used in a decision). ``observation_comments`` carries the v2
    structured fields as ``visibility=..; size=..; location=..; conditions=.. |
    description`` (``services.gemini_presence.format_verdict_comment``) until the
    media_evidence table exists; a v1 answer keeps the bare description.
    """
    from app.services.gemini_presence import format_verdict_comment

    row = {
        "id": str(uuid.uuid4()),
        "deployment_id": deployment_id,
        "media_id": media["id"],
        "observation_level": "media",
        "observation_type": "animal" if verdict.has_animal else "blank",
        "classifier_category": "animal" if verdict.has_animal else "blank",
        "source_type": "ai",
        "ai_origin": "cloud",
        "source_model_version": model,
        "review_status": "ai_reviewed",
        "classification_method": "machine",
        "classified_by": model,
        "classification_timestamp": timestamp,
    }
    if getattr(verdict, "confidence", None) is not None:
        row["confidence"] = round(min(1.0, max(0.0, float(verdict.confidence))), 4)
    if getattr(verdict, "prompt_version", "v1") != "v1":
        row["observation_comments"] = format_verdict_comment(verdict)[:500]
    elif verdict.description:
        row["observation_comments"] = verdict.description[:500]
    if verdict.has_animal and verdict.bbox is not None:
        x, y, w, h = verdict.bbox
        if all(0.0 <= v <= 1.0 for v in (x, y, w, h)) and w > 0 and h > 0:
            row.update(bbox_x=x, bbox_y=y, bbox_w=w, bbox_h=h)
    return row


def chunk_bursts(bursts: list[list[dict]], max_cells: int) -> list[list[dict]]:
    """Split each burst into contact-sheet-sized groups of at most ``max_cells`` frames (pure)."""
    return [burst[i : i + max_cells] for burst in bursts for i in range(0, len(burst), max_cells)]


def gemini_evidence_signals(verdict) -> dict[str, Any]:
    """The media_evidence signals one Gemini verdict contributes (pure; report section 8).

    ``gemini_presence`` is ``has_animal`` as 0/1; the visibility-weighted fusion
    input is derived later by ``burst_evidence``. A v1 verdict has no labels and
    contributes ``gemini_presence`` only.
    """
    from app.services.gemini_presence import SIZE_VALUE, VISIBILITY_VALUE

    signals: dict[str, Any] = {"gemini_presence": 1.0 if verdict.has_animal else 0.0}
    if verdict.prompt_version != "v1":
        signals["gemini_visibility"] = (VISIBILITY_VALUE.get(verdict.animal_visibility, 0.0), verdict.animal_visibility)
        signals["gemini_size"] = (SIZE_VALUE.get(verdict.animal_size, 0.0), verdict.animal_size)
    return signals


class GeminiPresenceStep(PipelineStep):
    """Ask Gemini whether each frame contains an animal; one observation row per frame.

    Runs before SpeciesNet on EVERY frame of the batch and writes its own
    ``animal``/``blank`` row tagged with the Gemini model id. It never touches
    SpeciesNet's rows: for now the two run side by side so
    ``scripts/eval_presence.py`` can compare their recall on labelled frames.
    Gated on ``FF_GEMINI_PRESENCE_ENABLED`` and a non-empty ``GEMINI_API_KEY``
    (both must be set on the ARQ worker). Idempotent: a media row that already
    has an observation for this model is skipped.

    Config overrides: ``gemini_model``, ``gemini_variant`` (single |
    contact_sheet | batch), ``gemini_thinking_level``, ``gemini_batch_max_wait_seconds``.
    """

    step_type = PipelineStepType.GEMINI_PRESENCE
    model_version = None  # the Gemini model id, resolved at run time

    async def run(
        self,
        media: list[dict],
        deployment_id: str,
        config: dict[str, Any],
    ) -> PipelineStepResult:
        from app.config import settings
        from app.domain.burst_evidence import group_bursts
        from app.domain.media_resolver import resolve_media
        from app.services import gemini_presence as gp
        from app.services.media_evidence import signal_rows, write_signals

        start = time.monotonic()
        model = config.get("gemini_model") or settings.GEMINI_PRESENCE_MODEL
        variant = config.get("gemini_variant") or settings.GEMINI_PRESENCE_VARIANT
        self.model_version = model
        if not settings.FF_GEMINI_PRESENCE_ENABLED or not gp.is_enabled():
            logger.info("gemini_presence_step_skipped_disabled", deployment_id=deployment_id)
            return PipelineStepResult(step=self.step_type, media_processed=0, model_version=model)
        if variant not in gp.VARIANTS:
            raise ValueError(f"GEMINI_PRESENCE_VARIANT must be one of {gp.VARIANTS}, got {variant!r}")

        svc = create_service_client()
        media_ids = [m["id"] for m in media]

        def _already_done() -> set[str]:
            if not media_ids:
                return set()
            resp = svc.table("observations").select("media_id").in_("media_id", media_ids).eq("source_model_version", model).execute()
            return {r["media_id"] for r in (resp.data or [])}

        done = await asyncio.to_thread(_already_done)
        todo = [m for m in media if m["id"] not in done]

        errors = 0
        frames: dict[str, bytes] = {}
        for m in todo:
            try:
                resolved = await resolve_media(m["file_path"], size="full")
                if not resolved:
                    errors += 1
                    continue
                frames[m["id"]] = resolved[0]
            except Exception as exc:
                logger.warning("gemini_presence_resolve_error", media_id=m.get("id"), error=str(exc))
                errors += 1
        resolvable = [m for m in todo if m["id"] in frames]

        # One call per frame, or one per burst chunk for the contact sheet (the same
        # bursts evidence fusion and the motion-ROI crop see).
        if variant == "contact_sheet":
            groups = chunk_bursts(group_bursts(resolvable, settings.BURST_GAP_SECONDS), gp.SHEET_MAX_CELLS)
        else:
            groups = [[m] for m in resolvable]

        thinking_level = config.get("gemini_thinking_level")

        def _call_all() -> list:
            payloads = [[frames[m["id"]] for m in g] for g in groups]
            if variant == "batch":
                return gp.presence_batch(
                    payloads,
                    variant,
                    model,
                    thinking_level=thinking_level,
                    max_wait_seconds=float(config.get("gemini_batch_max_wait_seconds", gp.BATCH_MAX_WAIT_SECONDS)),
                    display_name=f"ww-presence-{deployment_id[:8]}",
                )
            out = []
            for payload in payloads:
                try:
                    out.append(gp.presence(payload, variant, model, thinking_level=thinking_level))
                except Exception as exc:  # one failed call must not sink the batch
                    logger.warning("gemini_presence_call_error", model=model, error=str(exc))
                    out.append(gp.PresenceResult(model=model, variant=variant, verdicts=[None] * len(payload), error=str(exc)))
            return out

        results = await asyncio.to_thread(_call_all) if groups else []

        timestamp = datetime.now(timezone.utc).isoformat()
        run_id = str(uuid.uuid4())
        rows: list[dict] = []
        evidence: list[dict] = []
        input_tokens = output_tokens = 0
        total_cost = 0.0
        for group, result in zip(groups, results):
            input_tokens += result.usage.input_tokens
            output_tokens += result.usage.billed_output_tokens
            total_cost += result.cost_usd
            if result.error:
                errors += 1
            for m, verdict in zip(group, result.verdicts):
                if verdict is None:
                    continue
                rows.append(build_gemini_presence_observation(m, deployment_id, verdict, model, timestamp))
                evidence.extend(
                    signal_rows(
                        m["id"],
                        deployment_id,
                        gemini_evidence_signals(verdict),
                        source="gemini",
                        # model id plus prompt version, so a v2 answer adds rows beside v1 instead of replacing them
                        source_version=f"{model}:{getattr(verdict, 'prompt_version', 'v1')}",
                        computed_at=timestamp,
                        run_id=run_id,
                    )
                )

        def _persist() -> tuple[int, int]:
            for i in range(0, len(rows), 50):
                svc.table("observations").insert(rows[i : i + 50]).execute()
            written = 0
            try:
                written = write_signals(svc, evidence, deployment_id)
            except Exception as exc:  # the observations are already in; evidence is best-effort
                logger.warning("gemini_presence_evidence_write_failed", error=str(exc))
            return len(rows), written

        observations_created, evidence_written = await asyncio.to_thread(_persist) if rows else (0, 0)

        duration = time.monotonic() - start
        logger.info(
            "gemini_presence_step_complete",
            deployment_id=deployment_id,
            model=model,
            variant=variant,
            media_processed=len(media),
            skipped_existing=len(done),
            calls=len(groups),
            observations_created=observations_created,
            evidence_rows=evidence_written,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=round(total_cost, 6),
            errors=errors,
            duration_seconds=round(duration, 2),
        )
        return PipelineStepResult(
            step=self.step_type,
            observations_created=observations_created,
            media_processed=len(media),
            errors=errors,
            duration_seconds=round(duration, 2),
            model_version=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=round(total_cost, 6),
            counts={"evidence_rows": evidence_written},
        )


# ── Evidence Fusion Step (consensus verdict per frame) ───────────────

EVIDENCE_FUSION_VERSION = "evidence_fusion_v1"
BURSTS_VERSION = "bursts_v1"
MOTION_VERSION = "motion_v1"

_OBS_COLUMNS = (
    "id, media_id, observation_type, source_type, ai_origin, source_model_version, classified_by, "
    "confidence, classification_probability, classification_timestamp, observation_comments, scientific_name, vernacular_name"
)


def build_consensus_observation(
    media: dict,
    deployment_id: str,
    score: float,
    threshold: float,
    signals: dict[str, Any],
    timestamp: str,
) -> dict:
    """The consensus row for one media (pure; report section 7).

    Columns and CHECKs verified against ww-backend ``35_observations.sql``
    (2026-09-29): ``source_type`` in (ai, human, imported, consensus);
    ``ai_origin`` NULL allowed (``IS NULL OR IN (edge, cloud)``), and NULL is
    what a consensus row gets, so the key is left out; ``review_status``
    ``ai_reviewed`` (never ``consensus_approved``, which the active-learning
    QA treats as human truth); ``classification_method`` in (human, machine);
    ``observation_type`` in (animal, human, vehicle, blank, unknown);
    ``confidence`` on [0, 1]; ``observation_level`` in (media, event). No bbox,
    taxon, classifier_category or count: presence only in v1, the rest stays
    on the per-model rows. ``source_model_version`` is what
    ``delete_superseded_ai_observations`` keys on for the replace-not-append
    contract; ``classified_by`` carries the same value for readers.
    """
    from app.domain.burst_evidence import audit_line, consensus_type

    return {
        "id": str(uuid.uuid4()),
        "deployment_id": deployment_id,
        "media_id": media["id"],
        "observation_level": "media",
        "observation_type": consensus_type(score, signals.get("speciesnet_type"), threshold),
        "source_type": "consensus",
        "source_model_version": EVIDENCE_FUSION_VERSION,
        "review_status": "ai_reviewed",
        "confidence": round(min(1.0, max(0.0, float(score))), 4),
        "classification_method": "machine",
        "classified_by": EVIDENCE_FUSION_VERSION,
        "classification_timestamp": timestamp,
        "observation_comments": audit_line(score, threshold, signals, EVIDENCE_FUSION_VERSION)[:500],
    }


def fusion_evidence_signals(signals: dict[str, Any], score: float, threshold: float) -> dict[str, dict[str, Any]]:
    """The media_evidence rows the fusion step writes, grouped by source (pure; report section 8).

    SpeciesNet and Gemini signals are written by their own steps; edge signals
    are derived here from the edge rows because edge reflection has no step.
    """
    from app.domain.burst_evidence import WEIGHTS_VERSION

    return {
        "bursts": {
            "burst_id": signals.get("burst_id"),
            "burst_index": signals.get("burst_index"),
            "burst_len": signals.get("burst_len"),
            "burst_animal_count": signals.get("burst_animal_count"),
            "neighbour_animal": signals.get("neighbour_animal"),
        },
        "motion": {"motion_frac": signals.get("motion_frac")},
        "edge": {
            "edge_presence": signals.get("edge_presence"),
            "edge_score": None if signals.get("edge_score") is None else (signals.get("edge_score"), signals.get("edge_label")),
        },
        "fusion": {
            "evidence_score": score,
            "evidence_threshold": threshold,
            "evidence_weights_version": WEIGHTS_VERSION,
        },
    }


class EvidenceFusionStep(PipelineStep):
    """One consensus observation per frame from every signal the batch already has.

    Runs last. Re-reads the batch's media (with ``exif_metadata`` for the
    sequence tag and the edge NN scores) and their live observation rows, reads
    ``speciesnet_max_conf`` from ``media_evidence`` when that table exists,
    groups the batch into trigger bursts (``domain.burst_evidence.group_bursts``),
    computes frame-to-frame motion within each burst, scores every frame
    (``evidence_score``, weights v1) and writes ``source_type='consensus'`` rows
    tagged ``EVIDENCE_FUSION_VERSION``. Idempotent through
    ``delete_superseded_ai_observations`` (replace-not-append, keyed on our own
    ``source_model_version``); no other writer's rows are touched. The signals
    behind each score go to ``media_evidence`` when the table exists.

    Gated on ``FF_EVIDENCE_FUSION_ENABLED`` (set on the ARQ worker). Config
    overrides: ``evidence_threshold``, ``burst_gap_seconds``,
    ``confidence_threshold`` (the SpeciesNet cutoff used for near_threshold).
    """

    step_type = PipelineStepType.EVIDENCE_FUSION
    model_version = EVIDENCE_FUSION_VERSION

    async def run(
        self,
        media: list[dict],
        deployment_id: str,
        config: dict[str, Any],
    ) -> PipelineStepResult:
        import io

        from PIL import Image

        from app.config import settings
        from app.domain.burst_evidence import SUSPICIOUS_V1, band_of, burst_signals, evidence_score, group_bursts
        from app.domain.media_resolver import resolve_media
        from app.domain.motion_roi import compute_motion_fractions
        from app.services.media_evidence import read_signal, signal_rows, write_signals

        start = time.monotonic()
        if not settings.FF_EVIDENCE_FUSION_ENABLED:
            logger.info("evidence_fusion_step_skipped_disabled", deployment_id=deployment_id)
            return PipelineStepResult(step=self.step_type, media_processed=0, model_version=self.model_version)
        threshold = float(config.get("evidence_threshold", settings.EVIDENCE_FUSION_THRESHOLD))
        gap = float(config.get("burst_gap_seconds", settings.BURST_GAP_SECONDS))
        det_threshold = float(config.get("confidence_threshold", 0.2))
        svc = create_service_client()
        media_ids = [m["id"] for m in media]
        errors = 0

        def _load() -> tuple[list[dict], dict[str, list[dict]], dict[str, float]]:
            rows: list[dict] = []
            obs: dict[str, list[dict]] = {}
            for i in range(0, len(media_ids), 100):
                chunk = media_ids[i : i + 100]
                rows.extend(
                    svc.table("media").select("id, deployment_id, file_path, file_name, timestamp, exif_metadata").in_("id", chunk).execute().data
                    or []
                )
                for o in svc.table("observations").select(_OBS_COLUMNS).in_("media_id", chunk).is_("deleted_at", "null").execute().data or []:
                    obs.setdefault(o["media_id"], []).append(o)
            max_conf = read_signal(svc, media_ids, "speciesnet_max_conf", source="speciesnet")
            return rows, obs, max_conf

        media_rows, obs_by_media, max_conf = await asyncio.to_thread(_load) if media_ids else ([], {}, {})
        bursts = group_bursts(media_rows, gap)

        async def _motion(burst: list[dict]) -> list[Optional[float]]:
            """Per-frame motion fractions; None throughout when the burst is a singleton or nothing else resolved."""
            nonlocal errors
            if len(burst) < 2:
                return [None] * len(burst)
            images: list[Optional[Image.Image]] = []
            for m in burst:
                try:
                    resolved = await resolve_media(m["file_path"], size="full")
                    images.append(Image.open(io.BytesIO(resolved[0])).convert("RGB") if resolved else None)
                except Exception as exc:
                    logger.warning("evidence_fusion_resolve_error", media_id=m.get("id"), error=str(exc))
                    errors += 1
                    images.append(None)
            if sum(im is not None for im in images) < 2:
                return [None] * len(burst)
            try:
                fracs = await asyncio.to_thread(compute_motion_fractions, images)
            except Exception as exc:
                logger.warning("evidence_fusion_motion_error", burst=[m.get("id") for m in burst], error=str(exc))
                errors += 1
                return [None] * len(burst)
            return [f if im is not None else None for f, im in zip(fracs, images)]

        timestamp = datetime.now(timezone.utc).isoformat()
        run_id = str(uuid.uuid4())
        consensus: list[dict] = []
        evidence: list[dict] = []
        bands = {"animal": 0, "suspicious": 0, "confirmed_blank": 0}
        camera_shift = 0
        for burst in bursts:
            fracs = await _motion(burst)
            for m, signals in zip(burst, burst_signals(burst, obs_by_media, fracs, max_conf, det_threshold)):
                score, _contributions = evidence_score(signals)
                consensus.append(build_consensus_observation(m, deployment_id, score, threshold, signals, timestamp))
                bands[band_of(score, threshold, SUSPICIOUS_V1)] += 1
                camera_shift += bool(signals.get("camera_shift"))
                for source, values in fusion_evidence_signals(signals, score, threshold).items():
                    version = {"bursts": BURSTS_VERSION, "motion": MOTION_VERSION, "fusion": EVIDENCE_FUSION_VERSION}.get(
                        source, EVIDENCE_FUSION_VERSION
                    )
                    evidence.extend(
                        signal_rows(m["id"], deployment_id, values, source=source, source_version=version, computed_at=timestamp, run_id=run_id)
                    )

        def _persist() -> tuple[int, int]:
            delete_superseded_ai_observations(svc, [r["media_id"] for r in consensus], EVIDENCE_FUSION_VERSION)
            for i in range(0, len(consensus), 50):
                svc.table("observations").insert(consensus[i : i + 50]).execute()
            written = 0
            try:
                written = write_signals(svc, evidence, deployment_id)
            except Exception as exc:
                logger.warning("evidence_fusion_evidence_write_failed", error=str(exc))
            return len(consensus), written

        created, evidence_written = await asyncio.to_thread(_persist) if consensus else (0, 0)
        duration = time.monotonic() - start
        counts = {
            "bursts": len(bursts),
            "consensus_animal": bands["animal"],
            "consensus_blank": bands["suspicious"] + bands["confirmed_blank"],
            "suspicious": bands["suspicious"],
            "camera_shift": camera_shift,
            "evidence_rows": evidence_written,
        }
        logger.info(
            "evidence_fusion_step_complete",
            deployment_id=deployment_id,
            run_id=run_id,
            media_processed=len(media),
            threshold=threshold,
            weights=EVIDENCE_FUSION_VERSION,
            observations_created=created,
            errors=errors,
            duration_seconds=round(duration, 2),
            **counts,
        )
        return PipelineStepResult(
            step=self.step_type,
            observations_created=created,
            media_processed=len(media),
            errors=errors,
            duration_seconds=round(duration, 2),
            model_version=self.model_version,
            counts=counts,
        )


# ── SpeciesNet Step (detector + classifier) ──────────────────────────

# Confidence-based taxonomic roll-up: SpeciesNet's species guess is only trusted
# when the classification score clears SPECIES_CONFIDENCE; below that we back off
# to genus, then to the most specific available higher rank. This prevents shaky
# species-level claims (e.g. a 0.4 "Apteryx mantelli" recorded as "Apteryx").
SPECIES_CONFIDENCE = 0.5
GENUS_CONFIDENCE = 0.35


def rollup_taxon(
    taxonomy: dict,
    score: Optional[float],
    fallback_scientific: Optional[str] = None,
    fallback_vernacular: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Choose the (scientific_name, vernacular_name) at a confidence-appropriate rank.

    - score ≥ SPECIES_CONFIDENCE and a binomial exists → "Genus species" + common name.
    - score ≥ GENUS_CONFIDENCE and a genus exists → "Genus" (no common name).
    - otherwise → the most specific of family/order/class that is present.
    When ``taxonomy`` is empty (older predictions), fall back to the raw values.
    """
    if not taxonomy:
        return fallback_scientific, fallback_vernacular

    s = score if score is not None else 0.0
    genus = (taxonomy.get("genus") or "").strip()
    species = (taxonomy.get("species") or "").strip()
    common = taxonomy.get("common")

    if s >= SPECIES_CONFIDENCE and genus and species:
        return f"{genus} {species}".capitalize(), common
    if s >= GENUS_CONFIDENCE and genus:
        return genus.capitalize(), None
    for rank in ("family", "order", "class"):
        name = (taxonomy.get(rank) or "").strip()
        if name:
            return name.capitalize(), None
    # No confident higher rank — keep whatever binomial we have rather than nothing.
    if genus and species:
        return f"{genus} {species}".capitalize(), common
    return fallback_scientific, fallback_vernacular


def build_speciesnet_observations(
    media: dict,
    deployment_id: str,
    prediction,  # services.speciesnet_service.ImagePrediction (duck-typed)
    model_version: str,
    timestamp: str,
    confidence_threshold: float = 0.0,
    per_detection: bool = False,
) -> list[dict]:
    """Map a SpeciesNet ImagePrediction to CamtrapDP observation rows (pure).

    Detections below ``confidence_threshold`` are dropped; an image with no kept
    detections yields a single ``blank`` observation.

    SpeciesNet classifies **one species per image** but may emit several detection
    boxes. Two modes (``per_detection`` flag — wired to FF_PER_CROP_CLASSIFY_ENABLED):

    - **Collapsed (default):** same-type boxes collapse into **one** observation
      carrying a ``count`` (number of boxes) and the highest-confidence box as the
      representative bbox — fewer rows to review.
    - **Per-detection:** **one observation per box** (``count = 1``, its own bbox),
      so a crop + an independent classification can be attached to each animal
      (see the per-crop-classification spec). The image-level SpeciesNet species is
      a *provisional* label on each animal row, refined per-crop downstream.

    Either way the animal species is taxonomically rolled up (see ``rollup_taxon``);
    bbox fields are set as a complete quad or omitted (honours chk_bbox_complete).
    """
    base = {
        "deployment_id": deployment_id,
        "media_id": media["id"],
        "observation_level": "media",
        "source_type": "ai",
        # Cloud-pipeline provenance (mirrors edge_reflection's ai_origin='edge').
        # Makes ai_origin authoritative rather than relying on the NULL=cloud
        # display fallback; applies to animal and blank rows alike.
        "ai_origin": "cloud",
        "source_model_version": model_version,
        "review_status": "ai_reviewed",
        "classification_method": "machine",
        "classified_by": model_version,
        "classification_timestamp": timestamp,
    }

    kept = [d for d in prediction.detections if d.confidence >= confidence_threshold]
    if not kept:
        return [{**base, "id": str(uuid.uuid4()), "observation_type": "blank"}]

    sci_name, vern_name = rollup_taxon(
        getattr(prediction, "taxonomy", {}) or {},
        prediction.classification_score,
        prediction.scientific_name,
        prediction.common_name,
    )

    def _make_row(obs_type: str, representative, count: int) -> dict:
        row = {
            **base,
            "id": str(uuid.uuid4()),
            "observation_type": obs_type,
            "classifier_category": representative.category,
            "confidence": representative.confidence,
            "count": count,
        }
        if obs_type == "animal":
            row["scientific_name"] = sci_name
            row["vernacular_name"] = vern_name
            row["classification_probability"] = prediction.classification_score
        if representative.bbox is not None:
            x, y, w, h = representative.bbox
            row.update(bbox_x=x, bbox_y=y, bbox_w=w, bbox_h=h)
        return row

    if per_detection:
        # One observation per box — the substrate for per-crop classification.
        return [_make_row(det.observation_type, det, 1) for det in kept]

    # Collapse boxes by observation_type (animal / human / vehicle / unknown).
    by_type: dict[str, list] = {}
    for det in kept:
        by_type.setdefault(det.observation_type, []).append(det)
    return [_make_row(obs_type, max(dets, key=lambda d: d.confidence), len(dets)) for obs_type, dets in by_type.items()]


def speciesnet_evidence_signals(prediction, rows: list[dict]) -> dict[str, Any]:
    """``speciesnet_presence`` (a kept non-blank row) and ``speciesnet_max_conf`` over ALL detections (pure)."""
    detections = list(getattr(prediction, "detections", None) or [])
    max_conf = max((float(d.confidence) for d in detections), default=0.0)
    presence = any(r.get("observation_type") in ("animal", "human", "vehicle", "unknown") for r in rows)
    return {"speciesnet_presence": 1.0 if presence else 0.0, "speciesnet_max_conf": min(1.0, max(0.0, max_conf))}


def cloud_annotated_media_ids(ai_rows: list[dict]) -> set[str]:
    """Media the cloud pipeline has already annotated, from ``source_type='ai'`` rows.

    Camera AI rows (``ai_origin='edge'``) are ``source_type='ai'`` too, and they are
    written before the pipeline runs, so counting them would skip exactly the frames
    the camera flagged (#161). A NULL ``ai_origin`` is a cloud row from before the
    column existed.
    """
    return {r["media_id"] for r in ai_rows if r.get("ai_origin") != "edge"}


def delete_superseded_ai_observations(svc, media_ids, model_version: str) -> None:
    """Delete prior *machine* observations for these media + model version.

    Makes a (re)run replace rather than append: without this, every run on the
    same media inserts a fresh set of AI rows, so re-uploads / force reprocess /
    a re-run with a new model accumulate duplicate detections (the cause of the
    "10 identical human rows on one image" bug).

    Only rows still in the ``ai_reviewed`` state are removed. A human who
    confirms or edits an AI label keeps ``source_type='ai'`` but advances
    ``review_status`` to ``human_reviewed`` (see lib/observations) — those are
    preserved, so reprocessing never discards human work.

    Each removed observation's per-observation crop
    (``crops/{deployment}/{media}/{observation}.jpg``) is deleted from storage
    first so reprocessing doesn't leak orphaned crops. Storage cleanup is
    best-effort — a failure there never blocks the row deletion.
    """
    from app.config import settings

    ids = list(media_ids)
    bucket = settings.SUPABASE_MEDIA_BUCKET
    for i in range(0, len(ids), 100):
        chunk = ids[i : i + 100]
        # Identify the rows about to be deleted so their crops can be removed too.
        doomed = (
            svc.table("observations")
            .select("id, media_id, deployment_id")
            .in_("media_id", chunk)
            .eq("source_model_version", model_version)
            .eq("review_status", "ai_reviewed")
            .execute()
        ).data or []
        crop_paths = [f"crops/{r['deployment_id']}/{r['media_id']}/{r['id']}.jpg" for r in doomed if r.get("media_id") and r.get("deployment_id")]
        if crop_paths:
            try:
                svc.storage.from_(bucket).remove(crop_paths)
            except Exception as exc:  # best-effort; never block the row deletion
                logger.warning("superseded_crop_cleanup_failed", count=len(crop_paths), error=str(exc))
        (
            svc.table("observations")
            .delete()
            .in_("media_id", chunk)
            .eq("source_model_version", model_version)
            .eq("review_status", "ai_reviewed")
            .execute()
        )


class SpeciesNetStep(PipelineStep):
    """SpeciesNet ensemble — detection + species classification in one pass.

    Downloads each media item to a temp file (via the media resolver), runs the
    SpeciesNet ensemble, and writes media-level observations with bounding boxes,
    detection confidence, and a species guess. Images with no kept detection yield
    a single ``blank`` observation.
    """

    step_type = PipelineStepType.SPECIESNET
    model_version = SPECIESNET_VERSION

    async def run(
        self,
        media: list[dict],
        deployment_id: str,
        config: dict[str, Any],
    ) -> PipelineStepResult:
        import os
        import shutil
        import tempfile

        from app.domain.media_resolver import resolve_media
        from app.services.media_evidence import signal_rows, write_signals
        from app.services.speciesnet_service import get_speciesnet_service

        start = time.monotonic()
        threshold = config.get("confidence_threshold", 0.2)
        svc = create_service_client()
        errors = 0
        observations_created = 0
        evidence_written = 0

        tmpdir = tempfile.mkdtemp(prefix="speciesnet_")
        path_to_media: dict[str, dict] = {}
        try:
            # Resolve each media item to a local temp file for the model.
            for m in media:
                try:
                    resolved = await resolve_media(m["file_path"], size="full")
                    if not resolved:
                        errors += 1
                        continue
                    data, _content_type = resolved
                    path = os.path.join(tmpdir, f"{m['id']}.jpg")
                    with open(path, "wb") as fh:
                        fh.write(data)
                    path_to_media[path] = m
                except Exception as exc:
                    logger.warning("speciesnet_resolve_error", media_id=m.get("id"), error=str(exc))
                    errors += 1

            predictions = await get_speciesnet_service().predict(list(path_to_media.keys()))

            timestamp = datetime.now(timezone.utc).isoformat()
            from app.config import settings

            per_detection = settings.FF_PER_CROP_CLASSIFY_ENABLED
            obs_batch: list[dict] = []
            evidence: list[dict] = []
            run_id = str(uuid.uuid4())
            for pred in predictions:
                m = path_to_media.get(pred.filepath)
                if not m:
                    continue
                rows = build_speciesnet_observations(
                    m,
                    deployment_id,
                    pred,
                    self.model_version,
                    timestamp,
                    threshold,
                    per_detection=per_detection,
                )
                obs_batch.extend(rows)
                # Evidence signals (report section 6.1): the detector's best confidence over
                # ALL boxes, before the threshold filter drops the sub-threshold ones, so the
                # fusion step can compute near_threshold. The observation rows are unchanged.
                evidence.extend(
                    signal_rows(
                        m["id"],
                        deployment_id,
                        speciesnet_evidence_signals(pred, rows),
                        source="speciesnet",
                        source_version=self.model_version,
                        computed_at=timestamp,
                        run_id=run_id,
                    )
                )

            if obs_batch:
                # Replace, don't append: clear this model's prior machine rows for the
                # media we just re-ran, then insert the fresh set. Idempotent under
                # re-uploads / force reprocess; human-reviewed rows are kept.
                resolved_ids = {m["id"] for m in path_to_media.values()}

                def _persist() -> tuple[int, int]:
                    delete_superseded_ai_observations(svc, resolved_ids, self.model_version)
                    inserted = 0
                    for i in range(0, len(obs_batch), 50):
                        batch = obs_batch[i : i + 50]
                        svc.table("observations").insert(batch).execute()
                        inserted += len(batch)
                    written = 0
                    try:
                        written = write_signals(svc, evidence, deployment_id)
                    except Exception as exc:  # best-effort; the observations are already in
                        logger.warning("speciesnet_evidence_write_failed", error=str(exc))
                    return inserted, written

                observations_created, evidence_written = await asyncio.to_thread(_persist)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        duration = time.monotonic() - start
        logger.info(
            "speciesnet_step_complete",
            deployment_id=deployment_id,
            media_processed=len(media),
            observations_created=observations_created,
            evidence_rows=evidence_written,
            errors=errors,
            duration_seconds=round(duration, 2),
        )
        return PipelineStepResult(
            step=self.step_type,
            observations_created=observations_created,
            media_processed=len(media),
            errors=errors,
            duration_seconds=round(duration, 2),
            model_version=self.model_version,
            counts={"evidence_rows": evidence_written},
        )


# ── BioCLIP Step (secondary zero-shot classifier) ────────────────────


def build_bioclip_observations(
    media: dict,
    deployment_id: str,
    prediction,  # services.bioclip_service.CropPrediction (duck-typed)
    model_version: str,
    timestamp: str,
    confidence_threshold: float = 0.0,
) -> list[dict]:
    """Map a BioCLIP CropPrediction to a CamtrapDP observation row (pure).

    BioCLIP has no detector, so it never produces blanks — it only adds an
    ``animal`` observation carrying its species guess. The row is tagged with
    BioCLIP's own ``source_model_version`` so it sits alongside (not on top of)
    the SpeciesNet observation, enabling ensemble/disagreement views. Below the
    threshold, or with no name, nothing is emitted.
    """
    if not prediction.scientific_name:
        return []
    if prediction.score is not None and prediction.score < confidence_threshold:
        return []
    return [
        {
            "id": str(uuid.uuid4()),
            "deployment_id": deployment_id,
            "media_id": media["id"],
            "observation_level": "media",
            "observation_type": "animal",
            "source_type": "ai",
            "ai_origin": "cloud",  # cloud-pipeline provenance (see build_speciesnet_observations)
            "source_model_version": model_version,
            "review_status": "ai_reviewed",
            "classification_method": "machine",
            "classified_by": model_version,
            "classification_timestamp": timestamp,
            "scientific_name": prediction.scientific_name,
            "vernacular_name": prediction.common_name,
            "classification_probability": prediction.score,
            "confidence": prediction.score,
        }
    ]


def build_crop_classification_observation(
    detection: dict,
    deployment_id: str,
    prediction,  # services.bioclip_service.CropPrediction (duck-typed)
    model_version: str,
    timestamp: str,
    confidence_threshold: float = 0.0,
) -> Optional[dict]:
    """The per-crop classifier's own row for one detector observation (pure).

    The per-crop path (``FF_PER_CROP_CLASSIFY_ENABLED``) classifies each detection's
    crop and writes the result beside the detector's row, never onto it, so each
    model keeps its own verdict (#162). The row carries the detection's box and
    detection confidence, which is what links it to that detection, and the
    classifier's species, score and version. It has no ``crop_url``: the crop
    belongs to the detector's row.

    Returns ``None`` when the classifier has no usable name or scores below the
    threshold, as for the whole-image rows (``build_bioclip_observations``).
    """
    if not prediction.scientific_name:
        return None
    if prediction.score is not None and prediction.score < confidence_threshold:
        return None
    return {
        "id": str(uuid.uuid4()),
        "deployment_id": deployment_id,
        "media_id": detection["media_id"],
        "observation_level": "media",
        "observation_type": "animal",
        "source_type": "ai",
        "ai_origin": "cloud",
        "source_model_version": model_version,
        "review_status": "ai_reviewed",
        "classification_method": "machine",
        "classified_by": model_version,
        "classification_timestamp": timestamp,
        "scientific_name": prediction.scientific_name,
        "vernacular_name": prediction.common_name,
        "classification_probability": prediction.score,
        "confidence": detection.get("confidence"),
        "count": 1,
        "bbox_x": detection.get("bbox_x"),
        "bbox_y": detection.get("bbox_y"),
        "bbox_w": detection.get("bbox_w"),
        "bbox_h": detection.get("bbox_h"),
    }


class BioCLIPStep(PipelineStep):
    """Classify stage — labels animal crops with a pluggable classifier.

    The "Classify" node of the detect → crop → classify tree. Prefers each
    media's ``media_assets.animal_crop_url`` (produced by AnimalCropStep) and
    falls back to the full image. Writes one extra ``animal`` observation per
    crop tagged with the classifier's model version — a second opinion that
    complements SpeciesNet rather than replacing it.

    The classifier is resolved from the registry (``domain/classifiers.py``):
    BioCLIP by default, or whatever ``config['classifier']`` selects, so a
    project can route its crops to a custom species model. Keeps the BIOCLIP
    step type + flag for back-compat.

    Config overrides:
      - ``classifier``:    str       → registry id (default 'bioclip').
      - ``bioclip_labels``: list[str] → constrain to a custom label set.
      - ``bioclip_rank``:   str       → Tree-of-Life rank (default 'species').
      - ``confidence_threshold``: float (shared with the run).
    """

    step_type = PipelineStepType.BIOCLIP
    model_version = BIOCLIP_VERSION

    async def run(
        self,
        media: list[dict],
        deployment_id: str,
        config: dict[str, Any],
    ) -> PipelineStepResult:
        import os
        import shutil
        import tempfile

        from app.config import settings
        from app.domain.classifiers import resolve_classifier, resolve_classifier_name
        from app.domain.media_resolver import resolve_media

        start = time.monotonic()
        if not settings.FF_BIOCLIP_ENABLED:
            logger.info("classify_step_skipped_disabled", deployment_id=deployment_id)
            return PipelineStepResult(step=self.step_type, media_processed=0, model_version=self.model_version)

        # Resolve which classifier labels these crops (config → project → default).
        classifier = resolve_classifier(resolve_classifier_name(config))
        model_version = classifier.version

        threshold = config.get("confidence_threshold", 0.0)

        # Per-crop path: refine each per-detection observation in place rather than
        # adding one hero-crop second-opinion row per image (mixed-species frames get
        # a distinct species per animal). Pairs with build_speciesnet_observations(
        # per_detection=True). The hero-crop branch below is the legacy default.
        if settings.FF_PER_CROP_CLASSIFY_ENABLED:
            return await self._refine_crops_per_detection(media, deployment_id, config, classifier, model_version, threshold, start)

        svc = create_service_client()
        errors = 0
        observations_created = 0
        skipped_confident = 0

        # Prefer the animal crop (better signal) over the full frame.
        media_ids = [m["id"] for m in media]

        def _fetch_crops() -> dict[str, str]:
            resp = svc.table("media_assets").select("media_id, animal_crop_url").in_("media_id", media_ids).execute()
            return {r["media_id"]: r["animal_crop_url"] for r in (resp.data or []) if r.get("animal_crop_url")}

        crop_map = await asyncio.to_thread(_fetch_crops)

        # Avoid showing the user three rows for one cat: BioCLIP is a *second opinion*,
        # so only emit it where SpeciesNet was NOT already confident. Where SpeciesNet
        # produced a confident animal label, skip BioCLIP so a single observation shows.
        # Set config['bioclip_always']=True to keep the full ensemble (disagreement views).
        suppress_when_confident = not config.get("bioclip_always", False)

        def _fetch_confident_speciesnet() -> set[str]:
            if not media_ids:
                return set()
            resp = (
                svc.table("observations")
                .select("media_id")
                .in_("media_id", media_ids)
                .eq("source_type", "ai")
                .eq("observation_type", "animal")
                .like("source_model_version", "speciesnet%")
                .gte("confidence", SPECIES_CONFIDENCE)
                .execute()
            )
            return {r["media_id"] for r in (resp.data or [])}

        confident_ids = await asyncio.to_thread(_fetch_confident_speciesnet) if suppress_when_confident else set()

        tmpdir = tempfile.mkdtemp(prefix="bioclip_")
        path_to_media: dict[str, dict] = {}
        try:
            for m in media:
                source = crop_map.get(m["id"]) or m.get("file_path")
                if not source:
                    continue
                try:
                    resolved = await resolve_media(source, size="full")
                    if not resolved:
                        errors += 1
                        continue
                    data, _content_type = resolved
                    path = os.path.join(tmpdir, f"{m['id']}.jpg")
                    with open(path, "wb") as fh:
                        fh.write(data)
                    path_to_media[path] = m
                except Exception as exc:
                    logger.warning("bioclip_resolve_error", media_id=m.get("id"), error=str(exc))
                    errors += 1

            predictions = await classifier.classify(list(path_to_media.keys()), config)

            timestamp = datetime.now(timezone.utc).isoformat()
            obs_batch: list[dict] = []
            for pred in predictions:
                m = path_to_media.get(pred.filepath)
                if not m:
                    continue
                if m["id"] in confident_ids:
                    skipped_confident += 1
                    continue  # SpeciesNet already labelled this animal confidently — no redundant row
                obs_batch.extend(build_bioclip_observations(m, deployment_id, pred, model_version, timestamp, threshold))

            if obs_batch:
                # Replace this classifier's prior machine rows for the media it just
                # re-ran (keyed by the classifier's own model version, so SpeciesNet
                # rows are untouched), then insert fresh. Idempotent on re-run.
                written_ids = {o["media_id"] for o in obs_batch}

                def _persist():
                    delete_superseded_ai_observations(svc, written_ids, model_version)
                    inserted = 0
                    for i in range(0, len(obs_batch), 50):
                        batch = obs_batch[i : i + 50]
                        svc.table("observations").insert(batch).execute()
                        inserted += len(batch)
                    return inserted

                observations_created = await asyncio.to_thread(_persist)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        duration = time.monotonic() - start
        logger.info(
            "classify_step_complete",
            deployment_id=deployment_id,
            classifier=classifier.name,
            media_processed=len(media),
            observations_created=observations_created,
            skipped_confident_speciesnet=skipped_confident,
            errors=errors,
            duration_seconds=round(duration, 2),
        )
        return PipelineStepResult(
            step=self.step_type,
            observations_created=observations_created,
            media_processed=len(media),
            errors=errors,
            duration_seconds=round(duration, 2),
            model_version=model_version,
        )

    async def _refine_crops_per_detection(
        self,
        media: list[dict],
        deployment_id: str,
        config: dict[str, Any],
        classifier,
        model_version: str,
        threshold: float,
        start: float,
    ) -> PipelineStepResult:
        """Classify *each* per-detection animal crop into its own row beside the detector's.

        The ``FF_PER_CROP_CLASSIFY_ENABLED`` path. Each ``animal`` observation already
        carries its own ``crop_url`` (written by ``generate_observation_crops`` in the
        Animal-Crop step), so we run the classifier on every crop and write one
        classifier row per detection, with that detection's box
        (``build_crop_classification_observation``). The SpeciesNet row is never edited,
        so a cat+rat frame keeps SpeciesNet's two rows and gains the classifier's two
        (#162). This classifier's prior rows for the same media are replaced, as on the
        whole-image path; below-threshold or nameless crops add nothing.
        """
        import os
        import shutil
        import tempfile

        from app.domain.media_resolver import resolve_media

        svc = create_service_client()
        media_ids = [m["id"] for m in media]
        errors = 0
        observations_created = 0

        def _fetch_animal_crops() -> list[dict]:
            if not media_ids:
                return []
            resp = (
                svc.table("observations")
                .select("id, media_id, crop_url, confidence, bbox_x, bbox_y, bbox_w, bbox_h")
                .in_("media_id", media_ids)
                .eq("source_type", "ai")
                .eq("observation_type", "animal")
                .like("source_model_version", "speciesnet%")
                .execute()
            )
            return [r for r in (resp.data or []) if r.get("crop_url")]

        obs_rows = await asyncio.to_thread(_fetch_animal_crops)

        tmpdir = tempfile.mkdtemp(prefix="crop_classify_")
        path_to_obs: dict[str, dict] = {}
        try:
            for obs in obs_rows:
                try:
                    resolved = await resolve_media(obs["crop_url"], size="full")
                    if not resolved:
                        errors += 1
                        continue
                    data, _content_type = resolved
                    path = os.path.join(tmpdir, f"{obs['id']}.jpg")
                    with open(path, "wb") as fh:
                        fh.write(data)
                    path_to_obs[path] = obs
                except Exception as exc:
                    logger.warning("crop_classify_resolve_error", observation_id=obs.get("id"), error=str(exc))
                    errors += 1

            predictions = await classifier.classify(list(path_to_obs.keys()), config)
            timestamp = datetime.now(timezone.utc).isoformat()

            obs_batch: list[dict] = []
            for pred in predictions:
                obs = path_to_obs.get(pred.filepath)
                if not obs:
                    continue
                row = build_crop_classification_observation(obs, deployment_id, pred, model_version, timestamp, threshold)
                if row:
                    obs_batch.append(row)

            def _persist() -> int:
                # Replace this classifier's prior rows for the media it re-ran (keyed by its
                # own model version, so the SpeciesNet rows are untouched), then insert.
                delete_superseded_ai_observations(svc, {o["media_id"] for o in obs_batch}, model_version)
                for i in range(0, len(obs_batch), 50):
                    svc.table("observations").insert(obs_batch[i : i + 50]).execute()
                return len(obs_batch)

            if obs_batch:
                observations_created = await asyncio.to_thread(_persist)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        duration = time.monotonic() - start
        logger.info(
            "classify_crops_complete",
            deployment_id=deployment_id,
            classifier=classifier.name,
            crops_classified=len(path_to_obs),
            observations_created=observations_created,
            errors=errors,
            duration_seconds=round(duration, 2),
        )
        return PipelineStepResult(
            step=self.step_type,
            observations_created=observations_created,
            media_processed=len(media),
            errors=errors,
            duration_seconds=round(duration, 2),
            model_version=model_version,
        )


# ── Step Registry ────────────────────────────────────────────────────


_STEP_REGISTRY: dict[PipelineStepType, type[PipelineStep]] = {
    PipelineStepType.MEDIA_PREP: MediaPreparationStep,
    PipelineStepType.GEMINI_PRESENCE: GeminiPresenceStep,
    PipelineStepType.SPECIESNET: SpeciesNetStep,
    PipelineStepType.ANIMAL_CROP: AnimalCropStep,
    PipelineStepType.BIOCLIP: BioCLIPStep,
    PipelineStepType.EVIDENCE_FUSION: EvidenceFusionStep,
}


def get_step(step_type: PipelineStepType) -> PipelineStep:
    """Instantiate a pipeline step by its type."""
    cls = _STEP_REGISTRY.get(step_type)
    if cls is None:
        raise ValueError(f"Unknown pipeline step type: {step_type}")
    return cls()


# ── Pipeline Orchestrator ────────────────────────────────────────────


async def run_pipeline(
    deployment_id: str,
    steps: list[PipelineStepType],
    confidence_threshold: float = 0.2,
    config: dict[str, Any] | None = None,
    user_id: str | None = None,
    only_unannotated: bool = True,
    force: bool = False,
    media_ids: list[str] | None = None,
    on_step: Optional[Callable[[str, int, int], Awaitable[None]]] = None,
) -> PipelineRunResult:
    """Execute a sequence of pipeline steps on a deployment.

    1. Fetches all media for the deployment.
    2. Runs each step sequentially, passing the full media set.
    3. Records an annotation_run for provenance.
    4. Returns aggregate results.

    Args:
        deployment_id: UUID of the target deployment.
        steps: Ordered list of pipeline step types to execute.
        confidence_threshold: Minimum confidence to keep detections.
        config: Step-specific overrides.
        user_id: Authenticated user triggering the pipeline.

    Returns:
        PipelineRunResult with per-step and aggregate metrics.
    """
    # Guard: a non-UUID deployment_id can never match a real deployment — e.g. an
    # unresolved SD-card folder prefix like "00000000" from an unconfigured camera.
    # Passing it to Postgres raises 'invalid input syntax for type uuid', failing the
    # whole AI phase. Skip cleanly instead.
    try:
        uuid.UUID(str(deployment_id))
    except (ValueError, TypeError, AttributeError):
        logger.warning("pipeline_skipped_invalid_deployment_id", deployment_id=deployment_id)
        return PipelineRunResult(deployment_id=str(deployment_id))

    overall_start = time.monotonic()
    # Wall-clock start for the annotation_runs row. Must be set explicitly: if we let
    # started_at fall back to the DB default now(), it is evaluated at INSERT time —
    # after the Python-computed completed_at below — violating the table's
    # CHECK (completed_at >= started_at) constraint.
    run_started_at = datetime.now(timezone.utc)
    config = config or {}
    config["confidence_threshold"] = confidence_threshold
    svc = create_service_client()

    # Model versions whose observations this run would (re)create. Used for the
    # idempotency guard below — re-running the same model on the same media is a
    # no-op, so skip it and don't burn GPU.
    _step_versions = {
        PipelineStepType.SPECIESNET: SPECIESNET_VERSION,
        PipelineStepType.BIOCLIP: BIOCLIP_VERSION,
    }
    run_versions = [_step_versions[s] for s in steps if s in _step_versions]

    # 1. Fetch media for the deployment, applying the abuse/idempotency guards.
    #  - **Idempotency (always, unless force):** skip media already annotated by
    #    *this run's model versions*. This makes repeated triggers / re-uploads /
    #    even a manual `only_unannotated=False` re-run a no-op on identical
    #    content+model — the key defence against running AI on the same images
    #    continuously. ``force=True`` (privileged) is the only true reprocess.
    #  - **Incremental (only_unannotated, the default):** additionally skip any
    #    AI-annotated media, so a normal run only touches NEW images.
    def _fetch_media():
        q = svc.table("media").select("id, deployment_id, file_path, file_name, file_mediatype, timestamp").eq("deployment_id", deployment_id)
        # Scope to specific media when given (e.g. a CamtrapDP import runs AI only on the
        # image-backed rows — not the fileless CSV references that would choke media_prep).
        if media_ids:
            q = q.in_("id", media_ids)
        resp = q.order("timestamp").execute()
        rows = resp.data or []
        if force or not rows:
            return rows

        skip: set[str] = set()
        if run_versions:
            done = (
                svc.table("observations")
                .select("media_id")
                .eq("deployment_id", deployment_id)
                .in_("source_model_version", run_versions)
                .not_.is_("media_id", "null")
                .execute()
                .data
                or []
            )
            skip |= {o["media_id"] for o in done}
        if only_unannotated:
            ai = (
                svc.table("observations")
                .select("media_id, ai_origin")
                .eq("deployment_id", deployment_id)
                .eq("source_type", "ai")
                .not_.is_("media_id", "null")
                .execute()
                .data
                or []
            )
            skip |= cloud_annotated_media_ids(ai)
        return [m for m in rows if m["id"] not in skip]

    media = await asyncio.to_thread(_fetch_media)

    if not media:
        logger.warning("pipeline_no_media", deployment_id=deployment_id)
        return PipelineRunResult(deployment_id=deployment_id)

    # 2. Run each step
    step_results: list[PipelineStepResult] = []
    total_observations = 0

    for _idx, step_type in enumerate(steps):
        step = get_step(step_type)
        logger.info("pipeline_step_start", step=step_type.value, deployment_id=deployment_id)
        if on_step is not None:
            # Report step start so callers (e.g. the upload job) can surface granular
            # AI-pipeline progress + logs instead of a frozen bar.
            await on_step(step_type.value, _idx, len(steps))
        result = await step.run(media, deployment_id, config)
        step_results.append(result)
        total_observations += result.observations_created
        # One line per step with its time per frame, for the cost per photo (#171).
        logger.info(
            "pipeline_step_timing",
            step=step_type.value,
            deployment_id=deployment_id,
            media_processed=result.media_processed,
            duration_seconds=result.duration_seconds,
            seconds_per_frame=result.seconds_per_frame,
            gpu_model=step_type in GPU_MODEL_STEPS,
        )

    overall_duration = time.monotonic() - overall_start

    # 3. Record annotation run (best-effort provenance).
    # The observations are already committed by the steps above; a failure to write
    # this bookkeeping row must NOT mark the whole inference run as failed. model_id
    # (required by annotation_runs.chk_annotation_run_provenance for ai_inference) is
    # resolved from the steps via CLOUD_MODEL_IDS, whose rows are seeded in ww-backend
    # supabase/seeds/dev/data.sql. The try/except is a safety net for envs missing
    # those rows (or any other transient write failure).
    annotation_run_id = str(uuid.uuid4())
    run_model_id = _resolve_run_model_id(steps)

    def _record_run():
        svc.table("annotation_runs").insert(
            {
                "id": annotation_run_id,
                "deployment_id": deployment_id,
                "run_type": "ai_inference",
                "model_id": run_model_id,
                "config": {
                    "steps": [s.value for s in steps],
                    "confidence_threshold": confidence_threshold,
                    **config,
                },
                "observation_count": total_observations,
                "started_at": run_started_at.isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "created_by": user_id,
            }
        ).execute()

    try:
        await asyncio.to_thread(_record_run)
    except Exception as exc:  # noqa: BLE001 — provenance is non-critical
        logger.warning(
            "annotation_run_record_failed",
            deployment_id=deployment_id,
            observations=total_observations,
            error=str(exc),
        )

    logger.info(
        "pipeline_complete",
        deployment_id=deployment_id,
        steps=[s.value for s in steps],
        total_media=len(media),
        total_observations=total_observations,
        duration_seconds=round(overall_duration, 2),
        # What the GPU job bills per frame: the whole run, every step, divided by its frames.
        seconds_per_frame=round(overall_duration / len(media), 3),
    )

    return PipelineRunResult(
        deployment_id=deployment_id,
        annotation_run_id=annotation_run_id,
        steps=step_results,
        total_media=len(media),
        total_observations=total_observations,
        duration_seconds=round(overall_duration, 2),
    )
