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
- **First detection:** a deployment with a detection of the species in the period and no earlier
  photo of it there.
- **Reviewed detections** (Target 21): detections counted from the photos a person reviewed,
  beside what each project's GBIF dataset publishes (``domain/gbif_dataset.py``).

The photos are counted in the database (#456): ww-backend's ``gbf_report_counts`` (#362) returns
the photo, AI, review and reviewer counts, each flagged species' detections per deployment and
month with their first detections, and the reviewed detections per project. Its header lists the
counting rules; ``tests/gbf_reference.py`` keeps the Python they were ported from, which the
local-stack test compares with it. It counts only what the caller may read, so it is called with
the caller's session: as the service role it counts nothing. Here: camera-nights, rates, sites,
cameras, the taxa's names, the sorting and GBIF's figures. ``build_report`` is pure, so the tests
pin each figure on fixed counts; the projects, deployments and taxa are read with the service
role, so the router checks access first.

Dates are UTC days, the period's last day included.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.domain.gbif_dataset import with_gbif_figures
from app.services.db_utils import read_all, rows_of
from app.services.supabase_client import create_service_client

Row = Dict[str, Any]

METHOD_VERSION = "gbf-1"
THREATENED_STATUSES = ("Vulnerable", "Endangered", "Critically Endangered")
COUNTS_FUNCTION = "gbf_report_counts"

# Taxon ids in one ``in.(...)`` filter, to keep the URL short.
_CHUNK = 100
_TAXON_COLUMNS = "id, scientific_name, common_name, conservation_status, invasive_status"


@dataclass(frozen=True)
class Period:
    """UTC days ``first`` to ``last``, both included: ``[start, end)``."""

    start: datetime
    end: datetime

    @classmethod
    def of_days(cls, first: date, last: date) -> Period:
        return cls(datetime.combine(first, time(), timezone.utc), datetime.combine(last + timedelta(days=1), time(), timezone.utc))


def _ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


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


def _species_figures(
    taxon: Row,
    flagged: List[Row],
    first: List[Row],
    nights: float,
    nights_by_month: Dict[str, float],
    deployment_names: Dict[str, str],
) -> Row:
    """One flagged taxon's figures from its ``flagged`` rows (detections per deployment and month)
    and its ``flagged_first`` rows (first detection per deployment, and whether it was seen there
    before the period)."""
    by_month: Dict[str, int] = defaultdict(int)
    for f in flagged:
        by_month[str(f["month"])] += int(f["detections"])
    found = sum(by_month.values())
    firsts = []
    for f in first:
        when = _ts(f.get("first_detection_at"))
        if f.get("seen_before") or when is None:
            continue
        dep = str(f["deployment_id"])
        firsts.append({"deployment_id": dep, "deployment_name": deployment_names.get(dep), "date": when.date().isoformat()})
    return {
        "taxon_id": taxon["id"],
        "scientific_name": taxon.get("scientific_name"),
        "common_name": taxon.get("common_name"),
        "conservation_status": taxon.get("conservation_status"),
        "invasive": bool(taxon.get("invasive_status")),
        "detections": found,
        "rate": rate(found, nights),
        "sites_with": len({str(f["deployment_id"]) for f in flagged}),
        "monthly": [{"month": m, "detections": by_month.get(m, 0), "rate": rate(by_month.get(m, 0), n)} for m, n in nights_by_month.items()],
        "first_detections": sorted(firsts, key=lambda f: (f["date"], f["deployment_name"] or "")),
    }


def _target(species: List[Row], sites: int) -> Row:
    species.sort(key=lambda s: (s["rate"] is None, -(s["rate"] or 0), -s["detections"], s["scientific_name"] or ""))
    return {"sites_monitored": sites, "species_recorded": len(species), "species": species}


def _by_taxon(rows: Iterable[Row]) -> Dict[str, List[Row]]:
    out: Dict[str, List[Row]] = defaultdict(list)
    for r in rows:
        out[str(r["taxon_id"])].append(r)
    return out


def build_report(
    *,
    projects: List[Row],
    deployments: List[Row],
    counts: Row,
    taxa: List[Row],
    first: date,
    last: date,
    now: datetime,
) -> Row:
    """The report from the database's counts (``gbf_report_counts``), the projects' live
    deployments and the flagged taxa the counts name."""
    period = Period.of_days(first, last)
    nights = {str(d["id"]): camera_nights(d, period.start, period.end, now) for d in deployments}
    with_photos = {str(d) for d in counts.get("deployments_with_photos") or []}
    monitored = [d for d in deployments if nights[str(d["id"])] > 0 or str(d["id"]) in with_photos]
    total_nights = sum(nights.values())
    nights_by_month = {key: sum(camera_nights(d, start, end, now) for d in deployments) for key, start, end in months(period)}
    names = {str(d["id"]): d.get("name") or "" for d in deployments}

    by_id = {str(t["id"]): t for t in taxa}
    flagged = _by_taxon(counts.get("flagged") or [])
    firsts = _by_taxon(counts.get("flagged_first") or [])
    figures = [
        _species_figures(by_id.get(taxon_id, {"id": taxon_id}), rows, firsts.get(taxon_id, []), total_nights, nights_by_month, names)
        for taxon_id, rows in flagged.items()
    ]
    reviewed = {str(r["project_id"]): int(r["detections"]) for r in counts.get("reviewed_detections") or []}

    return {
        "method_version": METHOD_VERSION,
        "period": {"from": first.isoformat(), "to": last.isoformat()},
        "projects": [{"id": p["id"], "name": p.get("name")} for p in projects],
        "camera_nights_by_month": [{"month": m, "camera_nights": round(n, 2)} for m, n in nights_by_month.items()],
        "target_6": _target([f for f in figures if f["invasive"]], len(monitored)),
        "target_4": _target([f for f in figures if f["conservation_status"] in THREATENED_STATUSES], len(monitored)),
        "target_20": {
            "cameras": len({d.get("device_id") for d in monitored if d.get("device_id")}),
            "sites": len(monitored),
            "camera_nights": round(total_nights, 2),
            "photos": int(counts.get("photos") or 0),
            "photos_with_ai": int(counts.get("photos_with_ai") or 0),
            "photos_reviewed": int(counts.get("photos_reviewed") or 0),
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
        "target_22": {"reviewers": int(counts.get("reviewers") or 0)},
    }


# ── Reads ───────────────────────────────────────────────────────────


def _chunks(ids: List[str], size: int = _CHUNK) -> Iterable[List[str]]:
    for i in range(0, len(ids), size):
        yield ids[i : i + size]


def read_counts(user_client, project_ids: List[str], period: Period) -> Row:
    """``gbf_report_counts`` for the projects over the period, called as the caller."""
    params = {"p_project_ids": project_ids, "p_from": period.start.isoformat(), "p_to": period.end.isoformat()}
    data = user_client.rpc(COUNTS_FUNCTION, params).execute().data
    if not isinstance(data, dict):
        raise RuntimeError(f"{COUNTS_FUNCTION} returned {type(data).__name__}, not an object")
    return data


def _taxa(svc, taxon_ids: List[str]) -> List[Row]:
    rows: List[Row] = []
    for chunk in _chunks(taxon_ids):
        rows += rows_of(svc.table("taxa").select(_TAXON_COLUMNS).in_("id", chunk).execute())
    return rows


def _read_report(user_client, project_ids: List[str], first: date, last: date) -> Optional[Row]:
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
    counts = read_counts(user_client, project_ids, Period.of_days(first, last))
    taxon_ids = sorted({str(f["taxon_id"]) for f in counts.get("flagged") or []})
    return build_report(
        projects=projects,
        deployments=deployments,
        counts=counts,
        taxa=_taxa(svc, taxon_ids),
        first=first,
        last=last,
        now=datetime.now(timezone.utc),
    )


async def gbf_report(user_client, project_ids: List[str], first: date, last: date) -> Optional[Row]:
    """The report for live projects, or None when one of them is not (deleted or unknown).

    ``user_client`` is the caller's session, for ``gbf_report_counts``. The projects, deployments
    and taxa are read with the service role: the caller's access to every project is checked
    before."""
    report = await asyncio.to_thread(_read_report, user_client, project_ids, first, last)
    if report is not None:
        report["target_21"]["datasets"] = await with_gbif_figures(report["target_21"]["datasets"])
    return report
