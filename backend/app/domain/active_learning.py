# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Active learning + QA (pure domain logic + orchestration).

Active learning ranks unreviewed media by information gain so reviewers see the
most valuable image first. QA is a *separate*, unbiased measure of label quality
(active learning is biased by design — never use it for accuracy estimates).

Composite AL score (weights from the embedding registry):
    0.35·novelty + 0.35·uncertainty + 0.20·disagreement + 0.10·outlier
where (per media):
    novelty      = 1 − HDBSCAN cluster_confidence (low membership prob = novel)
    uncertainty  = 1 − max AI confidence            (hard classifier case)
    disagreement = 1 if AI label ≠ human label       (else 0)
    outlier      = 1 if HDBSCAN outlier              (else 0)

The combine + QA helpers are pure (unit-tested); persistence is lazy.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

import structlog

from app.registries.embedding_registry import ACTIVE_LEARNING_WEIGHTS
from app.services.db_utils import rows_of

logger = structlog.get_logger()

# Rows per bulk-upsert request when persisting recomputed AL scores.
_PERSIST_CHUNK = 500


# ── Pure helpers ─────────────────────────────────────────────────────


def combine_al_score(novelty: float, uncertainty: float, disagreement: float, is_outlier: bool) -> float:
    """Weighted active-learning score in [0, 1] (higher = review sooner)."""
    w = ACTIVE_LEARNING_WEIGHTS
    score = (
        w["novelty"] * _clamp(novelty)
        + w["uncertainty"] * _clamp(uncertainty)
        + w["disagreement"] * _clamp(disagreement)
        + w["outlier_boost"] * (1.0 if is_outlier else 0.0)
    )
    return round(_clamp(score), 4)


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _norm_label(label: Optional[str]) -> Optional[str]:
    return label.strip().lower() if isinstance(label, str) and label.strip() else None


def compute_qa_metrics(pairs: list[tuple[Optional[str], Optional[str]]]) -> dict:
    """AI-vs-human agreement over (ai_label, human_label) pairs (pure).

    Only pairs where BOTH labels exist are counted. This is an agreement-based
    precision proxy; a fully unbiased QA needs a blind random-sample review
    workflow (documented follow-up).
    """
    both = [(_norm_label(a), _norm_label(h)) for a, h in pairs]
    both = [(a, h) for a, h in both if a and h]
    matches = sum(1 for a, h in both if a == h)
    precision = round(matches / len(both), 4) if both else None
    return {"n_compared": len(both), "matches": matches, "precision": precision}


# ── Orchestration ────────────────────────────────────────────────────


# A label counts as human-provided when a person made it OR validated it — mirrors the
# canonical treatment in inaturalist_publish / wildlife_brain. Imported/consensus data keeps
# its original source_type ("imported", "ai"…) but carries a human review_status, so checking
# source_type alone misses it and corrupts the AI-vs-human scoring below.
_HUMAN_REVIEWED = {"human_reviewed", "expert_reviewed", "consensus_approved"}


def _ai_human_labels(observations: list[dict]) -> dict[str, dict]:
    """Group observations by media_id → {ai_label, ai_conf, human_label}."""
    by_media: dict[str, dict] = {}
    for o in observations:
        mid = o.get("media_id")
        if not mid:
            continue
        slot = by_media.setdefault(mid, {"ai_label": None, "ai_conf": 0.0, "human_label": None})
        label = o.get("scientific_name") or o.get("vernacular_name")
        is_human = o.get("source_type") == "human" or o.get("review_status") in _HUMAN_REVIEWED
        if is_human:
            slot["human_label"] = label or slot["human_label"]
        elif o.get("source_type") == "ai":
            conf = float(o.get("confidence") or 0.0)
            if conf >= slot["ai_conf"]:
                slot["ai_conf"] = conf
                slot["ai_label"] = label
    return by_media


async def _fetch_observations(deployment_id: str) -> list[dict]:
    from app.services.supabase_client import create_service_client

    svc = create_service_client()

    def _fetch():
        resp = (
            svc.table("observations")
            .select("media_id, source_type, review_status, confidence, scientific_name, vernacular_name")
            .eq("deployment_id", deployment_id)
            .is_("deleted_at", "null")
            .execute()
        )
        return rows_of(resp)

    return await asyncio.to_thread(_fetch)


async def recompute_scores(deployment_id: str, progress=None) -> int:
    """Recompute active_learning_score for all embedded media in a deployment."""
    from app.services.supabase_client import create_service_client

    svc = create_service_client()

    def _fetch_embeddings():
        resp = svc.table("media_embeddings").select("media_id, cluster_confidence, is_outlier").eq("deployment_id", deployment_id).execute()
        return rows_of(resp)

    embeddings = await asyncio.to_thread(_fetch_embeddings)
    if not embeddings:
        return 0
    labels = _ai_human_labels(await _fetch_observations(deployment_id))

    now = datetime.now(timezone.utc).isoformat()
    updated = 0

    def _persist(scored: list[tuple[str, float]]):
        # Bulk upsert instead of one round-trip per row: media_id is the PK, so
        # PostgREST merges just these columns into the existing rows. Rows were
        # fetched from this table moments ago; if one vanished mid-run the
        # insert path fails loudly on the deployment_id NOT NULL constraint
        # (deliberately not supplied — never resurrect a deleted row).
        payload = [{"media_id": media_id, "active_learning_score": score, "al_score_updated_at": now} for media_id, score in scored]
        for i in range(0, len(payload), _PERSIST_CHUNK):
            svc.table("media_embeddings").upsert(payload[i : i + _PERSIST_CHUNK]).execute()

    scored: list[tuple[str, float]] = []
    for row in embeddings:
        mid = row["media_id"]
        lab = labels.get(mid, {})
        novelty = 1.0 - float(row.get("cluster_confidence") or 0.0)
        uncertainty = 1.0 - float(lab.get("ai_conf") or 0.0)
        ai_l, hu_l = _norm_label(lab.get("ai_label")), _norm_label(lab.get("human_label"))
        disagreement = 1.0 if (ai_l and hu_l and ai_l != hu_l) else 0.0
        score = combine_al_score(novelty, uncertainty, disagreement, bool(row.get("is_outlier")))
        scored.append((mid, score))

    await asyncio.to_thread(_persist, scored)
    updated = len(scored)
    if progress:
        await progress(1.0, f"Scored {updated} media")
    logger.info("al_recompute_complete", deployment_id=deployment_id, updated=updated)
    return updated


async def get_review_queue(deployment_id: str, limit: int = 50) -> list[dict]:
    """Top media by active_learning_score DESC, enriched with the AI label."""
    from app.services.supabase_client import create_service_client

    svc = create_service_client()

    def _fetch():
        resp = (
            svc.table("media_embeddings")
            .select("media_id, active_learning_score, cluster_id, is_outlier, cluster_confidence")
            .eq("deployment_id", deployment_id)
            .order("active_learning_score", desc=True)
            .limit(limit)
            .execute()
        )
        return rows_of(resp)

    rows = await asyncio.to_thread(_fetch)
    labels = _ai_human_labels(await _fetch_observations(deployment_id))
    for r in rows:
        lab = labels.get(r["media_id"], {})
        r["ai_label"] = lab.get("ai_label")
        r["ai_confidence"] = lab.get("ai_conf")
        r["human_label"] = lab.get("human_label")
    return rows


async def qa_report(deployment_id: str) -> dict:
    """AI-vs-human agreement report for a deployment (precision proxy)."""
    labels = _ai_human_labels(await _fetch_observations(deployment_id))
    pairs = [(v.get("ai_label"), v.get("human_label")) for v in labels.values()]
    metrics = compute_qa_metrics(pairs)
    return {"deployment_id": deployment_id, **metrics, "method": "ai_human_agreement"}
