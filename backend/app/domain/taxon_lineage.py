# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Broader and narrower names: whether one label's taxon contains another's (label checks
decision 17, documentation/development reports/2026-11_label-checks.md).

``Rattus`` and ``Rattus rattus`` are not a conflict: the genus contains the species. Lineage comes
from the ``taxa`` row of each label's ``taxon_id`` (its rank and its kingdom to species columns).
A label without a ``taxa`` row compares by name: the same name, or a name with one more word (a
genus contains its species, a species its subspecies).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from app.services.db_utils import rows_of

# What a containment test reads off a taxa row (ww-backend supabase/schemas/public/tables/34a_taxa.sql).
TAXA_COLUMNS = "id, scientific_name, rank, kingdom, phylum, class, order_name, family, genus, species"

# The column of a narrower taxon's row that names its ancestor at each rank.
_RANK_COLUMN = {
    "kingdom": "kingdom",
    "phylum": "phylum",
    "class": "class",
    "order": "order_name",
    "family": "family",
    "genus": "genus",
    "species": "species",
}

# Taxon ids per ``in.(...)`` read.
_CHUNK = 100


@dataclass(frozen=True)
class Named:
    """A label's name and, when it has one, its taxa row id."""

    scientific_name: str
    taxon_id: Optional[str] = None


def name_key(name: Optional[str]) -> str:
    """A scientific name compared without case or extra spaces."""
    return " ".join((name or "").split()).lower()


def read_taxa(svc, taxon_ids: Sequence[str]) -> dict[str, dict]:
    """The taxa rows of ``taxon_ids``, keyed by id."""
    ids = sorted({t for t in taxon_ids if t})
    out: dict[str, dict] = {}
    for i in range(0, len(ids), _CHUNK):
        for row in rows_of(svc.table("taxa").select(TAXA_COLUMNS).in_("id", ids[i : i + _CHUNK]).execute()):
            out[row["id"]] = row
    return out


def contains(broad: Named, narrow: Named, taxa: Mapping[str, dict]) -> bool:
    """True when ``narrow`` is ``broad`` or falls within it."""
    b, n = name_key(broad.scientific_name), name_key(narrow.scientific_name)
    if not b or not n:
        return False
    if b == n or (n.startswith(b + " ") and len(n.split()) == len(b.split()) + 1):
        return True
    broad_row, narrow_row = taxa.get(broad.taxon_id or ""), taxa.get(narrow.taxon_id or "")
    if not broad_row or not narrow_row:
        return False
    column = _RANK_COLUMN.get(broad_row.get("rank") or "")
    if column is None:
        return False
    ancestor = name_key(narrow_row.get(column))
    if column == "species" and ancestor and " " not in ancestor:
        # The species column may hold the epithet alone ("rattus").
        ancestor = name_key(f"{narrow_row.get('genus') or ''} {ancestor}")
    return bool(ancestor) and ancestor == name_key(broad_row.get("scientific_name"))


def related(a: Named, b: Named, taxa: Mapping[str, dict]) -> bool:
    """The same taxon, or one a broader or narrower name of the other."""
    return contains(a, b, taxa) or contains(b, a, taxa)


def mixes_species(names: Sequence[Named], taxa: Mapping[str, dict]) -> bool:
    """True when two of ``names`` are different species, neither containing the other."""
    return any(not related(a, b, taxa) for i, a in enumerate(names) for b in names[i + 1 :])
