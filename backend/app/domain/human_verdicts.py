# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""A photo's human verdict, written with the service role (UI audit decision 2, 21 Oct 2026).

A photo carries one human row (``source_type='human'``) beside its AI rows, which stay as the
models wrote them, so "AI said X, a person said Y" survives. The frontend writes that row for the
viewer and the grid (``lib/humanVerdict.ts``); this module writes it for the Wildlife Brain
routes. ``public_api.is_human_row`` and ``is_human_reviewed`` decide which rows count.

- ``confirm_cluster`` labels the members that carry no human verdict yet and skips the others,
  so a cluster never overwrites a person's own label. ``unconfirm_cluster`` takes its rows back.
- ``review_photos`` writes one decision on many photos: a new human row where a photo has none,
  else an update of the photo's human rows.

Writes go in requests of ``CHUNK`` rows with no transaction across them, so a failure part way
leaves the earlier chunks written. Running the action again skips or rewrites what is done.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from app.domain.public_api import is_human_reviewed, is_human_row
from app.services.db_utils import read_all, rows_of

# Rows per insert, update or delete request, and photo ids per ``in.(...)`` read.
CHUNK = 50

# What the verdict rule reads off a photo's rows.
_VERDICT_COLUMNS = "id, media_id, source_type, review_status, classification_method"

Decision = Literal["approve", "reassign", "expert"]


class ClusterStateError(Exception):
    """The cluster is not in the state the action needs. ``code`` is the API error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Reviewer:
    id: str
    email: Optional[str] = None


@dataclass(frozen=True)
class Label:
    taxon_id: Optional[str] = None
    scientific_name: Optional[str] = None
    vernacular_name: Optional[str] = None

    @property
    def named(self) -> bool:
        return bool(self.taxon_id or self.scientific_name or self.vernacular_name)

    def fields(self) -> dict[str, Any]:
        return {
            "observation_type": "animal",
            "taxon_id": self.taxon_id,
            "scientific_name": self.scientific_name,
            "vernacular_name": self.vernacular_name,
        }


@dataclass
class ConfirmResult:
    observation_ids: list[str]
    skipped: int


@dataclass
class UnconfirmResult:
    deleted: int
    kept: int


@dataclass
class ReviewResult:
    created: int = 0
    updated: int = 0
    observation_ids: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)


def has_human_verdict(rows: list[dict]) -> bool:
    """True when a person has ruled on the photo: a row they wrote, or a row they reviewed."""
    return any(is_human_row(o) or is_human_reviewed(o) for o in rows)


def _create_fields(reviewer: Reviewer, review_status: str, now: str) -> dict[str, Any]:
    """A new human row's provenance, as ``humanCreateFields`` in lib/observations.ts."""
    return {
        "source_type": "human",
        "review_status": review_status,
        "classification_method": "human",
        "classified_by": reviewer.email or reviewer.id,
        "classification_timestamp": now,
        "annotator_id": reviewer.id,
        "reviewer_id": reviewer.id,
    }


def _review_fields(reviewer: Reviewer, review_status: str, now: str) -> dict[str, Any]:
    """What a change to a human row stamps, as ``humanReviewFields``; the annotator stays."""
    return {
        "review_status": review_status,
        "classification_method": "human",
        "classified_by": reviewer.email or reviewer.id,
        "classification_timestamp": now,
        "reviewer_id": reviewer.id,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chunks(items: list, size: int = CHUNK) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _live_rows(svc, media_ids: list[str]) -> dict[str, list[dict]]:
    """Each photo's live observations, every page of them."""
    by_media: dict[str, list[dict]] = {m: [] for m in media_ids}
    for part in _chunks(media_ids):
        rows = read_all(
            lambda part=part: svc.table("observations").select(_VERDICT_COLUMNS).in_("media_id", part).is_("deleted_at", "null").order("id")
        )
        for r in rows:
            by_media.setdefault(r["media_id"], []).append(r)
    return by_media


def _insert(svc, rows: list[dict]) -> list[str]:
    """Inserts in chunks and returns the new rows' ids."""
    ids: list[str] = []
    for part in _chunks(rows):
        ids += [r["id"] for r in rows_of(svc.table("observations").insert(part).execute())]
    return ids


def _update(svc, ids: list[str], patch: dict[str, Any]) -> list[str]:
    """Updates in chunks and returns the ids changed."""
    changed: list[str] = []
    for part in _chunks(ids):
        changed += [r["id"] for r in rows_of(svc.table("observations").update(patch).in_("id", part).execute())]
    return changed


def _assignment(svc, assignment_id: str) -> Optional[dict]:
    rows = rows_of(svc.table("cluster_assignments").select("*").eq("id", assignment_id).limit(1).execute())
    return rows[0] if rows else None


def confirm_cluster(svc, assignment_id: str, label: Label, reviewer: Reviewer) -> Optional[ConfirmResult]:
    """Label a cluster's members as ``label`` and mark the cluster confirmed. None when there is no
    such cluster.

    A member a person has already ruled on (``has_human_verdict``) keeps its label and counts in
    ``skipped``. A confirmed cluster raises ``ALREADY_CONFIRMED``: a second confirm would leave the
    cluster's taxon and its photos' rows out of step, and the undo could not find its rows.
    """
    a = _assignment(svc, assignment_id)
    if a is None:
        return None
    if a.get("review_state") == "confirmed":
        raise ClusterStateError("ALREADY_CONFIRMED", "This cluster is already confirmed.")
    # Every member: a read without .range() stops at 1,000, which labelled only 1,000.
    members = [
        m["media_id"]
        for m in read_all(
            lambda: (
                svc.table("media_embeddings")
                .select("media_id")
                .eq("deployment_id", a["deployment_id"])
                .eq("embedding_run_id", a["embedding_run_id"])
                .eq("cluster_id", a["cluster_id"])
                .order("media_id")
            )
        )
    ]
    rows = _live_rows(svc, members)
    held = {m for m in members if has_human_verdict(rows[m])}
    now = _now()
    ids = _insert(
        svc,
        [
            {
                "deployment_id": a["deployment_id"],
                "media_id": m,
                "observation_level": "media",
                **label.fields(),
                **_create_fields(reviewer, "human_reviewed", now),
                "embedding_run_id": a["embedding_run_id"],
                "cluster_id": a["cluster_id"],
            }
            for m in members
            if m not in held
        ],
    )
    svc.table("cluster_assignments").update(
        {
            "taxon_id": label.taxon_id,
            "scientific_name": label.scientific_name,
            "review_state": "confirmed",
            "confirmed_by": reviewer.id,
            "confirmed_at": now,
        }
    ).eq("id", assignment_id).execute()
    return ConfirmResult(observation_ids=ids, skipped=len(held))


def _as_confirmed(row: dict, a: dict) -> bool:
    """The row still says what the confirm wrote."""
    return (
        row.get("observation_type") == "animal"
        and row.get("review_status") == "human_reviewed"
        and row.get("taxon_id") == a.get("taxon_id")
        and row.get("scientific_name") == a.get("scientific_name")
    )


def unconfirm_cluster(svc, assignment_id: str) -> Optional[UnconfirmResult]:
    """Undo ``confirm_cluster``: delete the human rows it wrote and reopen the cluster. None when
    there is no such cluster; ``NOT_CONFIRMED`` when it is not confirmed.

    The confirm's rows are the human rows with the cluster's deployment, run and cluster id. One a
    person has changed since (relabelled, blanked, flagged for an expert) is their label now and
    stays, counted in ``kept``. The cluster goes back to ``open`` with no taxon or confirmer.
    """
    a = _assignment(svc, assignment_id)
    if a is None:
        return None
    if a.get("review_state") != "confirmed":
        raise ClusterStateError("NOT_CONFIRMED", "This cluster is not confirmed, so there is nothing to undo.")
    rows = read_all(
        lambda: (
            svc.table("observations")
            .select("id, observation_type, taxon_id, scientific_name, review_status")
            .eq("deployment_id", a["deployment_id"])
            .eq("embedding_run_id", a["embedding_run_id"])
            .eq("cluster_id", a["cluster_id"])
            .eq("source_type", "human")
            .order("id")
        )
    )
    ours = [r["id"] for r in rows if _as_confirmed(r, a)]
    for part in _chunks(ours):
        svc.table("observations").delete().in_("id", part).execute()
    svc.table("cluster_assignments").update(
        {"review_state": "open", "taxon_id": None, "scientific_name": None, "confirmed_by": None, "confirmed_at": None}
    ).eq("id", assignment_id).execute()
    return UnconfirmResult(deleted=len(ours), kept=len(rows) - len(ours))


def review_photos(svc, media_ids: list[str], writable: list[str], decision: Decision, label: Label, reviewer: Reviewer) -> ReviewResult:
    """One reviewer decision on many photos, one human row each.

    The meaning of the per-photo route: ``approve`` and ``reassign`` make ``label`` the photo's
    verdict (``human_reviewed``), ``expert`` marks it ``expert_reviewed``, with ``label`` when it
    names a species. A photo with no human row gets one; a photo with human rows has them all
    updated, so it keeps one answer (``planVerdict`` in lib/humanVerdict.ts). Without a species an
    existing row keeps its own. Photos not in ``writable`` are skipped as ``not_writable``, missing
    or deleted photos as ``not_found``.
    """
    unique = list(dict.fromkeys(media_ids))
    allowed = set(writable)
    result = ReviewResult(skipped=[{"media_id": m, "reason": "not_writable"} for m in unique if m not in allowed])
    targets = [m for m in unique if m in allowed]

    deployment_of: dict[str, str] = {}
    for part in _chunks(targets):
        found = rows_of(svc.table("media").select("id, deployment_id").in_("id", part).is_("deleted_at", "null").execute())
        deployment_of.update({r["id"]: r["deployment_id"] for r in found})
    result.skipped += [{"media_id": m, "reason": "not_found"} for m in targets if m not in deployment_of]
    photos = [m for m in targets if m in deployment_of]

    rows = _live_rows(svc, photos)
    status = "expert_reviewed" if decision == "expert" else "human_reviewed"
    now = _now()
    fresh = [m for m in photos if not any(is_human_row(o) for o in rows[m])]
    human_ids = [o["id"] for m in photos for o in rows[m] if is_human_row(o)]

    inserted = _insert(
        svc,
        [
            {
                "deployment_id": deployment_of[m],
                "media_id": m,
                "observation_level": "media",
                **label.fields(),
                **_create_fields(reviewer, status, now),
            }
            for m in fresh
        ],
    )
    patch = {**(label.fields() if label.named else {}), **_review_fields(reviewer, status, now)}
    changed = set(_update(svc, human_ids, patch))

    result.created = len(inserted)
    result.updated = sum(1 for m in photos if any(o["id"] in changed for o in rows[m]))
    result.observation_ids = inserted + [i for i in human_ids if i in changed]
    return result
