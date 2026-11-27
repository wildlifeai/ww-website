# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Label checks against the database (#448): read a project's labels and fingerprints, compute the
flags (``label_checks``), and bring ww-backend's ``label_checks`` rows up to date.

Scope is the label's own project. Design decision 2 also lets a species with too few examples
borrow other projects' fingerprints, but decision 12 then wants the flag to say so, and
``label_checks`` has no column for it; until it has, nothing is borrowed.

The rows (ww-backend ``tables/60_label_checks.sql``; one per observation and kind) are written with
the service role, and a recompute treats them by their ``outcome``:

- open (``NULL``): updated while still flagged. Once not flagged, ``relabelled`` when the photo's
  label is no longer the one flagged, else deleted.
- ``checked``: kept, until the label or the embedding run it was computed from changes and it is
  flagged again, which opens it again.
- ``relabelled``: kept, unless the same observation is flagged again under a new label.

So the outcomes left are the record design decision 15 counts (``summary``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from app.domain.human_verdicts import CHUNK
from app.domain.label_checks import Flag, Labelled, Tuning, compute_flags
from app.domain.public_api import is_human_reviewed, photo_verdict
from app.domain.taxon_lineage import name_key, read_taxa
from app.registries.embedding_registry import DEFAULT_SERVER_MODEL
from app.services.db_utils import read_all, rows_of
from app.services.vector_store import parse_vector

# The Brain's fingerprints: DINOv3 ViT-H+ (design, "Assumed").
MODEL = DEFAULT_SERVER_MODEL.value
# A 1,280-d vector is about 15 KB of JSON, so a page of 200 is about 3 MB.
_VECTOR_PAGE = 200
_OBS_COLUMNS = (
    "id, media_id, observation_type, scientific_name, taxon_id, source_type, review_status, classification_method, created_at, media!inner(id)"
)
_CHECK_COLUMNS = "id, observation_id, kind, outcome, label_scientific_name, embedding_run_id"


@dataclass
class ProjectLabels:
    items: list[Labelled]
    vectors: np.ndarray
    # Every photo's current label name; None when it shows no species.
    label_of_media: dict[str, Optional[str]] = field(default_factory=dict)


@dataclass
class SyncResult:
    flagged: int = 0
    written: int = 0
    relabelled: int = 0
    deleted: int = 0


def _species(row: Optional[dict]) -> bool:
    return bool(row and row.get("scientific_name") and row.get("observation_type") in (None, "animal"))


def _deployments(svc, project_id: str) -> list[str]:
    query = svc.table("deployments").select("id").eq("project_id", project_id).is_("deleted_at", "null")
    return [r["id"] for r in read_all(lambda: query.order("id"))]


def read_project(svc, project_id: str) -> ProjectLabels:
    """The project's labelled photos that have a fingerprint, and every photo's current label."""
    rows_by_media: dict[str, list[dict]] = {}
    vectors: dict[str, tuple[Optional[str], list[float]]] = {}
    deployments = _deployments(svc, project_id)
    for i in range(0, len(deployments), CHUNK):
        part = deployments[i : i + CHUNK]
        for r in read_all(
            lambda part=part: (
                svc.table("observations")
                .select(_OBS_COLUMNS)
                .in_("deployment_id", part)
                .is_("deleted_at", "null")
                .is_("media.deleted_at", "null")
                .order("created_at")
                .order("id")
            )
        ):
            if r.get("media_id"):
                rows_by_media.setdefault(r["media_id"], []).append(r)
        for r in read_all(
            lambda part=part: (
                svc.table("media_embeddings")
                .select("media_id, embedding_run_id, embedding")
                .in_("deployment_id", part)
                .eq("embedding_model", MODEL)
                .order("media_id")
            ),
            _VECTOR_PAGE,
        ):
            vector = parse_vector(r.get("embedding"))
            if vector:
                vectors[r["media_id"]] = (r.get("embedding_run_id"), vector)

    items: list[Labelled] = []
    rows: list[list[float]] = []
    label_of_media: dict[str, Optional[str]] = {}
    for media_id, obs in rows_by_media.items():
        verdict, _ = photo_verdict(obs)
        species = verdict if _species(verdict) else None
        label_of_media[media_id] = species["scientific_name"] if species else None
        if species is None or media_id not in vectors:
            continue
        run_id, vector = vectors[media_id]
        items.append(Labelled(species["id"], media_id, species["scientific_name"], species.get("taxon_id"), is_human_reviewed(species), run_id))
        rows.append(vector)
    return ProjectLabels(items, np.asarray(rows, dtype=np.float32), label_of_media)


def _media_of(svc, observation_ids: list[str]) -> dict[str, str]:
    """The photo of each live observation among ``observation_ids``."""
    out: dict[str, str] = {}
    for i in range(0, len(observation_ids), CHUNK):
        query = svc.table("observations").select("id, media_id").in_("id", observation_ids[i : i + CHUNK]).is_("deleted_at", "null")
        out.update({r["id"]: r["media_id"] for r in rows_of(query.execute()) if r.get("media_id")})
    return out


def _row(project_id: str, flag: Flag, now: str) -> dict:
    return {
        "observation_id": flag.observation_id,
        "project_id": project_id,
        "kind": flag.kind,
        "score": flag.score,
        "other_scientific_name": flag.other_scientific_name,
        "other_taxon_id": flag.other_taxon_id,
        "reference": flag.reference,
        "embedding_run_id": flag.embedding_run_id,
        "label_scientific_name": flag.label_scientific_name,
        "computed_at": now,
        "checked_by": None,
        "checked_at": None,
        "outcome": None,
    }


def sync(svc, project_id: str, flags: list[Flag], labels: ProjectLabels) -> SyncResult:
    """Bring the project's ``label_checks`` rows up to ``flags`` (the rules are in the module doc)."""
    now = datetime.now(timezone.utc).isoformat()
    existing = read_all(lambda: svc.table("label_checks").select(_CHECK_COLUMNS).eq("project_id", project_id).order("id"))
    media_of = {it.observation_id: it.media_id for it in labels.items}
    media_of.update(_media_of(svc, [r["observation_id"] for r in existing if r["observation_id"] not in media_of]))

    def relabelled(row: dict) -> bool:
        media = media_of.get(row["observation_id"])
        return media is not None and name_key(labels.label_of_media.get(media)) != name_key(row.get("label_scientific_name"))

    new = {(f.observation_id, f.kind): f for f in flags}
    write: list[dict] = []
    mark: list[str] = []
    drop: list[str] = []
    for row in existing:
        flag = new.pop((row["observation_id"], row["kind"]), None)
        outcome = row.get("outcome")
        if flag is not None:
            changed = name_key(flag.label_scientific_name) != name_key(row.get("label_scientific_name"))
            if (
                outcome is None
                or (outcome == "checked" and (changed or flag.embedding_run_id != row.get("embedding_run_id")))
                or (outcome == "relabelled" and changed)
            ):
                write.append(_row(project_id, flag, now))
        elif outcome is None:
            (mark if relabelled(row) else drop).append(row["id"])
    write += [_row(project_id, f, now) for f in new.values()]

    for i in range(0, len(write), 500):
        svc.table("label_checks").upsert(write[i : i + 500], on_conflict="observation_id,kind").execute()
    for i in range(0, len(mark), CHUNK):
        svc.table("label_checks").update({"outcome": "relabelled", "checked_at": now}).in_("id", mark[i : i + CHUNK]).execute()
    for i in range(0, len(drop), CHUNK):
        svc.table("label_checks").delete().in_("id", drop[i : i + CHUNK]).execute()
    return SyncResult(flagged=len(flags), written=len(write), relabelled=len(mark), deleted=len(drop))


def recompute_project(svc, project_id: str, tuning: Tuning) -> SyncResult:
    """Compute a project's flags afresh and store them."""
    labels = read_project(svc, project_id)
    taxa = read_taxa(svc, [it.taxon_id for it in labels.items if it.taxon_id])
    flags = compute_flags(labels.items, labels.vectors, taxa, tuning)
    return sync(svc, project_id, flags, labels)


def summary(svc, project_id: str) -> dict:
    """The project's flags by outcome: the open ones by kind, and how many reviewers marked checked
    against how many were relabelled (design decision 15)."""
    rows = read_all(lambda: svc.table("label_checks").select("id, kind, outcome, computed_at").eq("project_id", project_id).order("id"))
    open_by_kind: dict[str, int] = {}
    for r in rows:
        if r.get("outcome") is None:
            open_by_kind[r["kind"]] = open_by_kind.get(r["kind"], 0) + 1
    return {
        "open": open_by_kind,
        "checked": sum(1 for r in rows if r.get("outcome") == "checked"),
        "relabelled": sum(1 for r in rows if r.get("outcome") == "relabelled"),
        "computed_at": max((r["computed_at"] for r in rows if r.get("computed_at")), default=None),
    }


def projects_of_deployments(svc, deployment_ids: list[str]) -> list[str]:
    """The live projects of ``deployment_ids``."""
    out: set[str] = set()
    ids = sorted(set(deployment_ids))
    for i in range(0, len(ids), CHUNK):
        query = svc.table("deployments").select("id, project_id").in_("id", ids[i : i + CHUNK]).is_("deleted_at", "null")
        out.update(r["project_id"] for r in rows_of(query.execute()) if r.get("project_id"))
    return sorted(out)


def projects_with_changed_labels(svc, since: str) -> list[str]:
    """The live projects with an observation written or changed since ``since`` (ISO time). One
    small read per project, each on the deployment index, rather than a scan of every change."""
    projects = read_all(lambda: svc.table("projects").select("id").is_("deleted_at", "null").order("id"))
    out: list[str] = []
    for p in projects:
        deployments = _deployments(svc, p["id"])
        for i in range(0, len(deployments), CHUNK):
            query = svc.table("observations").select("id").in_("deployment_id", deployments[i : i + CHUNK]).gte("updated_at", since)
            if rows_of(query.limit(1).execute()):
                out.append(p["id"])
                break
    return out
