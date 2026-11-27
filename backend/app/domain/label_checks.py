# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Label checks: which labels look wrong next to the DINOv3 fingerprints of the labels around them
(#448; design and decisions in documentation/development reports/2026-11_label-checks.md).

Pure numpy over one project's labelled photos, one label (the photo verdict) and one fingerprint
(``media_embeddings.embedding``) each. Distances are cosine distances.

- A species' reference is its confirmed labels, or all its labels when it has fewer than
  ``min_examples`` confirmed ones (``reference='ai'``); with fewer labels than that it has none.
- Odd one out: a label's mean distance to its ``k`` nearest reference fingerprints of its own
  species, as a robust z-score against the reference's own (leave-one-out) distances. Flagged at
  ``odd_z``; the score runs from 0.5 there to 1 at twice ``odd_z``.
- Look-alike: among the ``k`` nearest fingerprints of the pool (each species' reference, or all its
  labels when it has none), the share carrying a species that is neither this one nor a broader or
  narrower name of it (``taxon_lineage``). Flagged above one half; the score is that share, and the
  flag names the commonest such species.
- Could be more specific: as look-alike, for neighbours carrying a narrower name of this label.

Scores run from 0.5 to 1 for every kind, so the filter's one slider applies to them all.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterator, Mapping, Optional, Sequence

import numpy as np

from app.domain.taxon_lineage import Named, contains, name_key, related

ODD_ONE_OUT = "odd_one_out"
LOOK_ALIKE = "look_alike"
MORE_SPECIFIC = "more_specific"

# Rows of the similarity matrix computed at once: 1,024 by 20,000 photos is 80 MB.
_BLOCK = 1024
# The spread of a species' distances never counts as less than this, so a reference of near
# duplicates does not flag every other photo of its species.
_MIN_SCALE = 0.01


@dataclass(frozen=True)
class Labelled:
    """A photo's label, the observation its card shows."""

    observation_id: str
    media_id: str
    scientific_name: str
    taxon_id: Optional[str] = None
    confirmed: bool = False
    embedding_run_id: Optional[str] = None


@dataclass(frozen=True)
class Flag:
    observation_id: str
    kind: str
    score: float
    reference: str
    label_scientific_name: str
    embedding_run_id: Optional[str] = None
    other_scientific_name: Optional[str] = None
    other_taxon_id: Optional[str] = None


@dataclass(frozen=True)
class Tuning:
    k: int = 5
    min_examples: int = 5
    odd_z: float = 3.0


def _unit_rows(vectors: np.ndarray) -> np.ndarray:
    x = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    return x / np.where(norms == 0, 1.0, norms)


def _blocks(rows: Sequence[int]) -> Iterator[list[int]]:
    for i in range(0, len(rows), _BLOCK):
        yield list(rows[i : i + _BLOCK])


def nearest(x: np.ndarray, rows: Sequence[int], pool: Sequence[int], k: int) -> tuple[np.ndarray, np.ndarray]:
    """For each of ``rows``, the ``k`` nearest of ``pool`` (never itself), nearest first: their
    indices into ``x`` and their cosine distances. ``x`` holds unit rows."""
    pool_arr = np.asarray(pool)
    at = {p: j for j, p in enumerate(pool)}
    idx_out, dist_out = [], []
    for block in _blocks(rows):
        sims = x[block] @ x[pool_arr].T
        for i, r in enumerate(block):
            if r in at:
                sims[i, at[r]] = -np.inf
        top = np.argpartition(-sims, k - 1, axis=1)[:, :k] if k < sims.shape[1] else np.tile(np.arange(sims.shape[1]), (len(block), 1))
        order = np.take_along_axis(sims, top, axis=1).argsort(axis=1)[:, ::-1]
        top = np.take_along_axis(top, order, axis=1)
        idx_out.append(pool_arr[top])
        dist_out.append(1.0 - np.take_along_axis(sims, top, axis=1))
    return np.vstack(idx_out), np.vstack(dist_out)


def _reference(indices: list[int], items: Sequence[Labelled], min_examples: int) -> Optional[tuple[list[int], str]]:
    confirmed = [i for i in indices if items[i].confirmed]
    if len(confirmed) >= min_examples:
        return confirmed, "confirmed"
    if len(indices) >= min_examples:
        return indices, "ai"
    return None


def _odd_ones(x: np.ndarray, items: Sequence[Labelled], groups: Mapping[str, list[int]], tuning: Tuning) -> list[Flag]:
    flags: list[Flag] = []
    for indices in groups.values():
        ref = _reference(indices, items, tuning.min_examples)
        if ref is None:
            continue
        ref_rows, reference = ref
        k = min(tuning.k, len(ref_rows) - 1)
        if k < 1:
            continue
        baseline = nearest(x, ref_rows, ref_rows, k)[1].mean(axis=1)
        median = float(np.median(baseline))
        scale = max(1.4826 * float(np.median(np.abs(baseline - median))), _MIN_SCALE)
        distances = nearest(x, indices, ref_rows, k)[1].mean(axis=1)
        for i, d in zip(indices, distances):
            z = (float(d) - median) / scale
            if z >= tuning.odd_z:
                item = items[i]
                score = 0.5 + 0.5 * min(1.0, (z - tuning.odd_z) / tuning.odd_z)
                flags.append(Flag(item.observation_id, ODD_ONE_OUT, round(score, 4), reference, item.scientific_name, item.embedding_run_id))
    return flags


def _look_alikes(x: np.ndarray, items: Sequence[Labelled], groups: Mapping[str, list[int]], taxa: Mapping[str, dict], tuning: Tuning) -> list[Flag]:
    if len(groups) < 2:
        return []
    pool: list[int] = []
    reference_of: dict[str, str] = {}
    for key, indices in groups.items():
        ref = _reference(indices, items, tuning.min_examples)
        members = ref[0] if ref else indices
        pool += members
        reference_of[key] = ref[1] if ref else ("confirmed" if all(items[i].confirmed for i in indices) else "ai")
    k = min(tuning.k, len(pool) - 1)
    if k < 1:
        return []

    def name_of(indices: list[int]) -> Named:
        return Named(items[indices[0]].scientific_name, next((items[i].taxon_id for i in indices if items[i].taxon_id), None))

    named = {key: name_of(indices) for key, indices in groups.items()}
    relation: dict[tuple[str, str], str] = {}

    def relation_of(mine: str, other: str) -> str:
        if (mine, other) not in relation:
            a, b = named[mine], named[other]
            relation[(mine, other)] = "narrower" if contains(a, b, taxa) else "related" if related(a, b, taxa) else "other"
        return relation[(mine, other)]

    keys = [name_key(it.scientific_name) for it in items]
    flags: list[Flag] = []
    neighbours = nearest(x, list(range(len(items))), pool, k)[0]
    for i, row in enumerate(neighbours):
        others: Counter[str] = Counter()
        narrower: Counter[str] = Counter()
        for j in row:
            if keys[j] == keys[i]:
                continue
            kind = relation_of(keys[i], keys[j])
            if kind == "other":
                others[keys[j]] += 1
            elif kind == "narrower":
                narrower[keys[j]] += 1
        for votes, flag_kind in ((others, LOOK_ALIKE), (narrower, MORE_SPECIFIC)):
            share = sum(votes.values()) / len(row)
            if share > 0.5:
                top = votes.most_common(1)[0][0]
                item = items[i]
                flags.append(
                    Flag(
                        item.observation_id,
                        flag_kind,
                        round(share, 4),
                        reference_of[top],
                        item.scientific_name,
                        item.embedding_run_id,
                        named[top].scientific_name,
                        named[top].taxon_id,
                    )
                )
                break
    return flags


def compute_flags(items: Sequence[Labelled], vectors: np.ndarray, taxa: Mapping[str, dict], tuning: Tuning = Tuning()) -> list[Flag]:
    """Every flag on ``items``, whose fingerprints are the rows of ``vectors``."""
    if not items:
        return []
    x = _unit_rows(vectors)
    groups: dict[str, list[int]] = {}
    for i, item in enumerate(items):
        groups.setdefault(name_key(item.scientific_name), []).append(i)
    return _odd_ones(x, items, groups, tuning) + _look_alikes(x, items, groups, taxa, tuning)
