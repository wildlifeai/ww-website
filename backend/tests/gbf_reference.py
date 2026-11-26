# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The GBF report's photo counting in Python, the reference for ww-backend's ``gbf_report_counts``.

``domain/gbf_report.py`` counted the photos itself until #456 moved the counting into the
database (ww-backend#362). This is that code, kept as the reference the function was ported from:
``counts`` returns what the function returns, from the rows the function reads, so the unit tests
pin each figure through it and ``test_gbf_report_local_stack.py`` compares the two on the same
rows. Change it together with the function's header and ``public_api.photo_verdict``.

One rule follows the function rather than the old route (Victor, 26 Nov 2026): ``seen_before``
looks at every live photo before the period at the deployment, so pass all of them as
``earlier_media``. The route read the history only of deployments where a period photo was itself
found as a flagged taxon.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.domain.events import _cluster_temporal
from app.domain.gbf_report import Period, _ts
from app.domain.public_api import is_human_reviewed, photo_verdict
from app.services.db_utils import read_all

Row = Dict[str, Any]

GAP_MINUTES = 30

_CHUNK = 100
_OBSERVATION_COLUMNS = (
    "id, observation_type, scientific_name, taxon_id, review_status, source_type, classification_method, reviewer_id, annotator_id, created_at"
)
_MEDIA_SELECT = f"id, deployment_id, timestamp, observations({_OBSERVATION_COLUMNS})"


@dataclass(frozen=True)
class Photo:
    """What the report needs of one photo. ``species`` is the verdict's species key, ``taxon`` its
    flagged taxon, both None for an empty or unnamed photo."""

    deployment_id: str
    taken: datetime
    species: Optional[str]
    taxon: Optional[Row]
    ai: bool
    reviewed: bool
    reviewers: frozenset[str]


@dataclass(frozen=True)
class Detection:
    deployment_id: str
    species: str
    start: datetime


def _name(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_ai(o: Row) -> bool:
    """A machine row, the consensus row included (the frontend's ``isAiLabel``)."""
    return o.get("source_type") in ("ai", "consensus") or o.get("classification_method") == "machine" or o.get("review_status") == "ai_reviewed"


class Taxa:
    """The flagged taxa, found by an observation's ``taxon_id``, else its scientific name."""

    def __init__(self, rows: Iterable[Row]):
        self.by_id = {str(t["id"]): t for t in rows}
        self.by_name = {_name(t.get("scientific_name")): t for t in self.by_id.values()}

    def of(self, label: Row) -> Optional[Row]:
        if label.get("taxon_id"):
            return self.by_id.get(str(label["taxon_id"]))
        return self.by_name.get(_name(label.get("scientific_name")))


def photo_of(media: Row, taxa: Taxa) -> Optional[Photo]:
    """One photo's verdict and review facts, or None when it has no timestamp."""
    taken = _ts(media.get("timestamp"))
    if taken is None:
        return None
    obs = media.get("observations") or []
    label, empty = photo_verdict(obs)
    named = label if label and not empty and label.get("scientific_name") else None
    taxon = taxa.of(named) if named else None
    species = _name(taxon["scientific_name"] if taxon else named["scientific_name"]) if named else None
    reviewed = [o for o in obs if is_human_reviewed(o)]
    return Photo(
        deployment_id=str(media["deployment_id"]),
        taken=taken,
        species=species,
        taxon=taxon,
        ai=any(_is_ai(o) for o in obs),
        reviewed=bool(reviewed),
        reviewers=frozenset(str(r) for o in reviewed if (r := o.get("reviewer_id") or o.get("annotator_id"))),
    )


def detections(photos: Iterable[Photo]) -> List[Detection]:
    """Independent detections: each species' photos at each deployment, grouped by the 30-minute rule."""
    groups: Dict[tuple[str, str], List[Row]] = defaultdict(list)
    for p in photos:
        if p.species:
            groups[(p.deployment_id, p.species)].append({"media_timestamp": p.taken.isoformat(), "taken": p.taken})
    out = []
    for (deployment_id, species), rows in groups.items():
        for cluster in _cluster_temporal(rows, GAP_MINUTES):
            out.append(Detection(deployment_id, species, cluster[0]["taken"]))
    return sorted(out, key=lambda d: (d.start, d.deployment_id, d.species))


def _utc(when: datetime) -> str:
    """``first_detection_at`` as the function writes it: ISO 8601 in UTC, microseconds, ``Z``."""
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def counts(*, deployments: List[Row], media: List[Row], earlier_media: List[Row], taxa: List[Row], first: date, last: date) -> Row:
    """``gbf_report_counts``' result from rows already read: the projects' live ``deployments``, the
    period's live photos (``media``) and the earlier ones at the same deployments
    (``earlier_media``), each with its live observations in the order written, and the flagged
    ``taxa``."""
    period = Period.of_days(first, last)
    flagged = Taxa(taxa)
    photos = [p for m in media if (p := photo_of(m, flagged)) and period.start <= p.taken < period.end]
    seen_before = {(p.deployment_id, p.species) for m in earlier_media if (p := photo_of(m, flagged)) and p.species and p.taken < period.start}

    taxon_of: Dict[str, Row] = {}
    for p in photos:
        if p.species and p.taxon and p.species not in taxon_of:
            taxon_of[p.species] = p.taxon
    by_month: Dict[tuple[str, str, str], int] = defaultdict(int)
    first_at: Dict[tuple[str, str], Detection] = {}
    for d in detections(photos):
        if d.species not in taxon_of:
            continue
        taxon_id = str(taxon_of[d.species]["id"])
        by_month[(taxon_id, d.deployment_id, d.start.astimezone(timezone.utc).strftime("%Y-%m"))] += 1
        first_at.setdefault((taxon_id, d.deployment_id), d)  # detections come in time order

    project_of = {str(d["id"]): str(d["project_id"]) for d in deployments}
    reviewed: Dict[str, int] = defaultdict(int)
    for d in detections(p for p in photos if p.reviewed):
        reviewed[project_of[d.deployment_id]] += 1

    return {
        "photos": len(photos),
        "photos_with_ai": sum(p.ai for p in photos),
        "photos_reviewed": sum(p.reviewed for p in photos),
        "reviewers": len(set().union(*(p.reviewers for p in photos))),
        "deployments_with_photos": sorted({p.deployment_id for p in photos}),
        "flagged": [{"taxon_id": t, "deployment_id": dep, "month": month, "detections": n} for (t, dep, month), n in sorted(by_month.items())],
        "flagged_first": [
            {"taxon_id": t, "deployment_id": dep, "first_detection_at": _utc(d.start), "seen_before": (dep, d.species) in seen_before}
            for (t, dep), d in sorted(first_at.items())
        ],
        "reviewed_detections": [{"project_id": p, "detections": n} for p, n in sorted(reviewed.items())],
    }


# ── Reads, for the local-stack comparison ───────────────────────────


def read_media(svc, deployment_ids: List[str], end: datetime, start: Optional[datetime] = None) -> List[Row]:
    """Live photos at the deployments taken before ``end`` (and from ``start``), each with its live
    observations in the order they were written, which ``photo_verdict`` expects."""
    rows: List[Row] = []
    for i in range(0, len(deployment_ids), _CHUNK):
        chunk = deployment_ids[i : i + _CHUNK]

        def build(chunk=chunk):
            query = svc.table("media").select(_MEDIA_SELECT).in_("deployment_id", chunk).is_("deleted_at", "null")
            query = query.is_("observations.deleted_at", "null").lt("timestamp", end.isoformat())
            if start is not None:
                query = query.gte("timestamp", start.isoformat())
            return query.order("created_at", foreign_table="observations").order("id", foreign_table="observations").order("id")

        rows += read_all(build)
    return rows
