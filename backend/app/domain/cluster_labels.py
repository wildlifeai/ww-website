# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The species a Wildlife Brain group's members carry, and whether they mix (#450, label checks
decision 6).

A member's label is its photo verdict (``public_api.photo_verdict``, the label its card shows);
only animal labels with a name count. A group mixes species when two of its labels are neither the
same taxon nor a broader or narrower name of each other (``taxon_lineage``). ``unreviewed`` counts
the members a cluster confirm would relabel: those nobody has ruled on (``has_human_verdict``).
Computed from the group assignments in ``media_embeddings``; nothing is stored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from postgrest import CountMethod

from app.domain.human_verdicts import CHUNK, has_human_verdict
from app.domain.public_api import photo_verdict
from app.domain.taxon_lineage import Named, mixes_species, name_key, read_taxa
from app.services.db_utils import read_all, rows_of

# Clusters per request: the Clusters page shows ten, the grid's page rarely spans more.
MAX_CLUSTERS = 50

# Rows per page of read_all.
_PAGE = 1000

_COLUMNS = "id, media_id, observation_type, scientific_name, vernacular_name, taxon_id, source_type, review_status, classification_method, created_at"


@dataclass
class LabelCount:
    scientific_name: str
    vernacular_name: Optional[str] = None
    taxon_id: Optional[str] = None
    photos: int = 0
    unreviewed: int = 0


@dataclass
class ClusterLabels:
    id: str
    mixed: bool = False
    labels: list[LabelCount] = field(default_factory=list)


def _species(row: Optional[dict]) -> bool:
    """An animal label with a name: never a person, a vehicle or a blank."""
    return bool(row and row.get("scientific_name") and row.get("observation_type") in (None, "animal"))


def _member_rows(svc, deployment_id: str, media_ids: list[str]) -> dict[str, list[dict]]:
    """Each member's live observations in the order they were written. Reads the deployment's rows
    whole when that takes fewer requests than reading the members' in chunks, so a big cluster
    stays inside Lambda's 30 s."""
    by_media: dict[str, list[dict]] = {m: [] for m in media_ids}
    if not media_ids:
        return by_media

    def live():
        return svc.table("observations").select(_COLUMNS).eq("deployment_id", deployment_id).is_("deleted_at", "null")

    total = svc.table("observations").select("id", count=CountMethod.exact).eq("deployment_id", deployment_id).is_("deleted_at", "null")
    total = total.limit(1).execute().count or 0
    if total / _PAGE <= len(media_ids) / CHUNK:
        rows = read_all(lambda: live().order("created_at").order("id"), _PAGE)
    else:
        parts = [media_ids[i : i + CHUNK] for i in range(0, len(media_ids), CHUNK)]
        rows = [r for part in parts for r in read_all(lambda part=part: live().in_("media_id", part).order("created_at").order("id"), _PAGE)]
    for r in rows:
        if r.get("media_id") in by_media:
            by_media[r["media_id"]].append(r)
    return by_media


def _count(members: list[str], rows: dict[str, list[dict]]) -> list[LabelCount]:
    labels: dict[str, LabelCount] = {}
    for m in members:
        verdict, _ = photo_verdict(rows[m])
        if verdict is None or not _species(verdict):
            continue
        key = name_key(verdict["scientific_name"])
        label = labels.setdefault(key, LabelCount(scientific_name=verdict["scientific_name"]))
        label.vernacular_name = label.vernacular_name or verdict.get("vernacular_name")
        label.taxon_id = label.taxon_id or verdict.get("taxon_id")
        label.photos += 1
        label.unreviewed += 0 if has_human_verdict(rows[m]) else 1
    return sorted(labels.values(), key=lambda lc: (-lc.photos, name_key(lc.scientific_name)))


def cluster_labels(svc, deployment_id: str, assignment_ids: list[str]) -> list[ClusterLabels]:
    """The labels of each listed cluster of ``deployment_id``, in the order asked; a cluster of
    another deployment, or none, is left out."""
    query = svc.table("cluster_assignments").select("id, embedding_run_id, cluster_id").eq("deployment_id", deployment_id)
    found = rows_of(query.in_("id", assignment_ids).execute())
    by_id = {a["id"]: a for a in found if a.get("embedding_run_id")}
    if not by_id:
        return []
    runs = sorted({a["embedding_run_id"] for a in by_id.values()})
    cluster_ids = sorted({a["cluster_id"] for a in by_id.values()})
    members: dict[tuple[str, int], list[str]] = {}
    for m in read_all(
        lambda: (
            svc.table("media_embeddings")
            .select("media_id, embedding_run_id, cluster_id")
            .eq("deployment_id", deployment_id)
            .in_("embedding_run_id", runs)
            .in_("cluster_id", cluster_ids)
            .order("media_id")
        )
    ):
        members.setdefault((m["embedding_run_id"], m["cluster_id"]), []).append(m["media_id"])

    of = {a_id: members.get((a["embedding_run_id"], a["cluster_id"]), []) for a_id, a in by_id.items()}
    rows = _member_rows(svc, deployment_id, [m for ms in of.values() for m in ms])
    counted = {a_id: _count(ms, rows) for a_id, ms in of.items()}
    taxa = read_taxa(svc, [lc.taxon_id for labels in counted.values() for lc in labels if lc.taxon_id])
    return [
        ClusterLabels(id=a_id, mixed=mixes_species([Named(lc.scientific_name, lc.taxon_id) for lc in counted[a_id]], taxa), labels=counted[a_id])
        for a_id in dict.fromkeys(assignment_ids)
        if a_id in counted
    ]
