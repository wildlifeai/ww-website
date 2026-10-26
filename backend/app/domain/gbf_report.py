# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The GBF contribution report (#371): what the selected projects contribute to targets 6, 4, 20,
21 and 22 of the Kunming-Montreal Global Biodiversity Framework over a period.

Site-level evidence from wherever the cameras are, never the GBF's national indicators. The
definitions, which the guide's method section quotes (``gbf-contribution-report.md``):

- **Photo verdict:** the one the grid shows, ``public_api.photo_verdict``. Empty photos and
  deleted rows don't count, and a photo without a timestamp is in no period.
- **Detection:** photos of one species at one deployment, each within 30 minutes of the one
  before, count as one, the rule of ``events._cluster_temporal``.
- **Camera-nights:** the days a deployment's [start, end or now] overlaps the period, as
  ``events.compute_deployment_effort`` counts trap nights. It assumes the camera ran throughout.
- **Rate:** detections per 100 camera-nights. **Site:** a deployment.
- **Species flags** come from ``taxa``, through ``observations.taxon_id``, else the scientific
  name: invasive is ``invasive_status`` (New Zealand), threatened is an IUCN
  ``conservation_status`` of Vulnerable or worse. A species can be both.
- **Reviewed detections** (Target 21): detections counted from the photos a person reviewed,
  beside what each project's GBIF dataset publishes (``domain/gbif_dataset.py``).

Dates are UTC days, the period's last day included. ``build_report`` is pure, so the tests pin
each figure on fixed data; ``gbf_report`` reads the rows with the service role, so the router
checks access first.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.domain.events import _cluster_temporal
from app.domain.gbif_dataset import with_gbif_figures
from app.domain.public_api import is_human_reviewed, photo_verdict
from app.services.db_utils import read_all, rows_of
from app.services.supabase_client import create_service_client

Row = Dict[str, Any]

METHOD_VERSION = "gbf-1"
GAP_MINUTES = 30
THREATENED_STATUSES = ("Vulnerable", "Endangered", "Critically Endangered")

# Deployment ids in one ``in.(...)`` filter, to keep the URL short.
_CHUNK = 100
_OBSERVATION_COLUMNS = (
    "id, observation_type, scientific_name, taxon_id, review_status, source_type, classification_method, reviewer_id, annotator_id, created_at"
)
_MEDIA_SELECT = f"id, deployment_id, timestamp, observations({_OBSERVATION_COLUMNS})"
_TAXON_COLUMNS = "id, scientific_name, common_name, conservation_status, invasive_status"


@dataclass(frozen=True)
class Period:
    """UTC days ``first`` to ``last``, both included: ``[start, end)``."""

    start: datetime
    end: datetime

    @classmethod
    def of_days(cls, first: date, last: date) -> Period:
        return cls(datetime.combine(first, time(), timezone.utc), datetime.combine(last + timedelta(days=1), time(), timezone.utc))


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


def _ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _name(value: Any) -> str:
    return str(value or "").strip().lower()


def camera_nights(deployment: Row, start: datetime, end: datetime, now: datetime) -> float:
    """Days the deployment's ``[deployment_start, deployment_end or now]`` overlaps ``[start, end)``."""
    began = _ts(deployment.get("deployment_start"))
    if began is None:
        return 0.0
    ended = _ts(deployment.get("deployment_end")) or now
    return max(0.0, (min(ended, end) - max(began, start)).total_seconds() / 86400)


def months(period: Period) -> List[tuple[str, datetime, datetime]]:
    """``(YYYY-MM, start, end)`` of each calendar month in the period, clipped to it."""
    out = []
    cur = datetime(period.start.year, period.start.month, 1, tzinfo=timezone.utc)
    while cur < period.end:
        nxt = datetime(cur.year + cur.month // 12, cur.month % 12 + 1, 1, tzinfo=timezone.utc)
        out.append((cur.strftime("%Y-%m"), max(cur, period.start), min(nxt, period.end)))
        cur = nxt
    return out


def rate(detections: int, nights: float) -> Optional[float]:
    """Detections per 100 camera-nights, or None with no camera-nights."""
    return round(detections / nights * 100, 2) if nights > 0 else None


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


def _species_figures(
    taxon: Row,
    found: List[Detection],
    nights: float,
    nights_by_month: Dict[str, float],
    seen_before: set[tuple[str, str]],
    deployment_names: Dict[str, str],
) -> Row:
    by_deployment: Dict[str, List[Detection]] = defaultdict(list)
    by_month: Dict[str, int] = defaultdict(int)
    for d in found:
        by_deployment[d.deployment_id].append(d)
        by_month[d.start.strftime("%Y-%m")] += 1
    first = [
        {"deployment_id": dep, "deployment_name": deployment_names.get(dep), "date": dets[0].start.date().isoformat()}
        for dep, dets in by_deployment.items()
        if (dep, found[0].species) not in seen_before
    ]
    return {
        "taxon_id": taxon["id"],
        "scientific_name": taxon.get("scientific_name"),
        "common_name": taxon.get("common_name"),
        "conservation_status": taxon.get("conservation_status"),
        "invasive": bool(taxon.get("invasive_status")),
        "detections": len(found),
        "rate": rate(len(found), nights),
        "sites_with": len(by_deployment),
        "monthly": [{"month": m, "detections": by_month.get(m, 0), "rate": rate(by_month.get(m, 0), n)} for m, n in nights_by_month.items()],
        "first_detections": sorted(first, key=lambda f: (f["date"], f["deployment_name"] or "")),
    }


def reviewed_detections(photos: Iterable[Photo], deployments: List[Row]) -> Dict[str, int]:
    """Detections of any species from the photos a person reviewed, per project."""
    project_of = {str(d["id"]): str(d["project_id"]) for d in deployments}
    out: Dict[str, int] = defaultdict(int)
    for d in detections(p for p in photos if p.reviewed):
        out[project_of.get(d.deployment_id, "")] += 1
    return out


def _target(species: List[Row], sites: int) -> Row:
    species.sort(key=lambda s: (s["rate"] is None, -(s["rate"] or 0), -s["detections"], s["scientific_name"] or ""))
    return {"sites_monitored": sites, "species_recorded": len(species), "species": species}


def build_report(
    *,
    projects: List[Row],
    deployments: List[Row],
    media: List[Row],
    earlier_media: List[Row],
    taxa: List[Row],
    first: date,
    last: date,
    now: datetime,
) -> Row:
    """The report from rows already read: the period's photos (``media``), the earlier photos at
    the same deployments (``earlier_media``, for first detections) and the flagged taxa."""
    period = Period.of_days(first, last)
    flagged = Taxa(taxa)
    photos = [p for m in media if (p := photo_of(m, flagged)) and period.start <= p.taken < period.end]
    seen_before = {(p.deployment_id, p.species) for m in earlier_media if (p := photo_of(m, flagged)) and p.species and p.taken < period.start}

    nights = {str(d["id"]): camera_nights(d, period.start, period.end, now) for d in deployments}
    with_photos = {p.deployment_id for p in photos}
    monitored = [d for d in deployments if nights[str(d["id"])] > 0 or str(d["id"]) in with_photos]
    total_nights = sum(nights.values())
    nights_by_month = {key: sum(camera_nights(d, start, end, now) for d in deployments) for key, start, end in months(period)}
    names = {str(d["id"]): d.get("name") or "" for d in deployments}

    taxon_of: Dict[str, Row] = {}
    for p in photos:
        if p.species and p.taxon and p.species not in taxon_of:
            taxon_of[p.species] = p.taxon
    found: Dict[str, List[Detection]] = defaultdict(list)
    for d in detections(photos):
        if d.species in taxon_of:
            found[d.species].append(d)
    figures = {sp: _species_figures(taxon_of[sp], dets, total_nights, nights_by_month, seen_before, names) for sp, dets in found.items()}
    reviewed = reviewed_detections(photos, deployments)

    return {
        "method_version": METHOD_VERSION,
        "period": {"from": first.isoformat(), "to": last.isoformat()},
        "projects": [{"id": p["id"], "name": p.get("name")} for p in projects],
        "camera_nights_by_month": [{"month": m, "camera_nights": round(n, 2)} for m, n in nights_by_month.items()],
        "target_6": _target([f for f in figures.values() if f["invasive"]], len(monitored)),
        "target_4": _target([f for f in figures.values() if f["conservation_status"] in THREATENED_STATUSES], len(monitored)),
        "target_20": {
            "cameras": len({d.get("device_id") for d in monitored if d.get("device_id")}),
            "sites": len(monitored),
            "camera_nights": round(total_nights, 2),
            "photos": len(photos),
            "photos_with_ai": sum(p.ai for p in photos),
            "photos_reviewed": sum(p.reviewed for p in photos),
        },
        # GBIF's own figures are added by gbf_report, which may call out.
        "target_21": {
            "reviewed_detections": sum(reviewed.values()),
            "datasets": [
                {
                    "project_id": p["id"],
                    "project_name": p.get("name"),
                    "dataset_key": p.get("gbif_dataset_key"),
                    "reviewed_detections": reviewed.get(str(p["id"]), 0),
                }
                for p in projects
            ],
        },
        "target_22": {"reviewers": len(set().union(*(p.reviewers for p in photos)))},
    }


# ── Reads ───────────────────────────────────────────────────────────


def _chunks(ids: List[str], size: int = _CHUNK) -> Iterable[List[str]]:
    for i in range(0, len(ids), size):
        yield ids[i : i + size]


def _read_media(svc, deployment_ids: List[str], end: datetime, start: Optional[datetime] = None) -> List[Row]:
    """Live photos at the deployments taken before ``end`` (and from ``start``), each with its live
    observations in the order they were written, which ``photo_verdict`` expects."""
    rows: List[Row] = []
    for chunk in _chunks(deployment_ids):

        def build(chunk=chunk):
            query = svc.table("media").select(_MEDIA_SELECT).in_("deployment_id", chunk).is_("deleted_at", "null")
            query = query.is_("observations.deleted_at", "null").lt("timestamp", end.isoformat())
            if start is not None:
                query = query.gte("timestamp", start.isoformat())
            return query.order("created_at", foreign_table="observations").order("id", foreign_table="observations").order("id")

        rows += read_all(build)
    return rows


def _flagged_taxa(svc) -> List[Row]:
    invasive = read_all(lambda: svc.table("taxa").select(_TAXON_COLUMNS).eq("invasive_status", True).order("id"))
    threatened = read_all(lambda: svc.table("taxa").select(_TAXON_COLUMNS).in_("conservation_status", list(THREATENED_STATUSES)).order("id"))
    return list({str(t["id"]): t for t in invasive + threatened}.values())


def _read_report(project_ids: List[str], first: date, last: date) -> Optional[Row]:
    svc = create_service_client()
    projects = rows_of(
        svc.table("projects").select("id, name, gbif_dataset_key").in_("id", project_ids).is_("deleted_at", "null").order("name").execute()
    )
    if len(projects) != len(set(project_ids)):
        return None
    deployments = read_all(
        lambda: (
            svc.table("deployments")
            .select("id, name, project_id, device_id, deployment_start, deployment_end")
            .in_("project_id", project_ids)
            .is_("deleted_at", "null")
            .order("id")
        )
    )
    period = Period.of_days(first, last)
    media = _read_media(svc, [str(d["id"]) for d in deployments], period.end, period.start)
    taxa = _flagged_taxa(svc)
    # Only deployments with a flagged species in the period need their history, for first detections.
    flagged = Taxa(taxa)
    look_back = sorted({p.deployment_id for m in media if (p := photo_of(m, flagged)) and p.taxon})
    earlier = _read_media(svc, look_back, period.start) if look_back else []
    return build_report(
        projects=projects,
        deployments=deployments,
        media=media,
        earlier_media=earlier,
        taxa=taxa,
        first=first,
        last=last,
        now=datetime.now(timezone.utc),
    )


async def gbf_report(project_ids: List[str], first: date, last: date) -> Optional[Row]:
    """The report for live projects, or None when one of them is not (deleted or unknown).

    Reads with the service role: the caller's access to every project is checked before."""
    report = await asyncio.to_thread(_read_report, project_ids, first, last)
    if report is not None:
        report["target_21"]["datasets"] = await with_gbif_figures(report["target_21"]["datasets"])
    return report
