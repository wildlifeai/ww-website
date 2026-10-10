# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""An in-memory stand-in for the PostgREST calls the public API makes, for scoping tests.

It evaluates ``select`` with embeds (``name!inner(...)``, nested), filters on the table or on
an embed (``projects.organisation_id``), ``order`` (also ``foreign_table``), ``range``,
``limit`` and ``count="exact"``, as PostgREST does for these cases: a filter on an embed trims
that embed, and drops the parent row only when the embed is ``!inner``. ``update`` and
``upsert`` are enough for the API key and job stores.

A column the table does not have fails the test, so a query naming a column that is not in
ww-backend's schema is caught here and not in production. ``COLUMNS`` lists the columns of
each table the API reads, from ww-backend dev's supabase/schema.sql (10 Oct 2026).
"""

from __future__ import annotations

import copy
import re
from types import SimpleNamespace
from typing import Any, Optional

COLUMNS = {
    "projects": {"id", "name", "organisation_id", "deleted_at", "created_at"},
    "deployments": {
        "id",
        "name",
        "project_id",
        "device_id",
        "device_eui",
        "deployment_status_id",
        "location_name",
        "deployment_start",
        "deleted_at",
        "created_at",
    },
    "deployment_statuses": {"id", "value"},
    "devices": {"id", "name", "bluetooth_id", "device_eui", "organisation_id", "deleted_at", "created_at"},
    "media": {"id", "deployment_id", "file_path", "timestamp", "deleted_at", "created_at"},
    "observations": {
        "id",
        "deployment_id",
        "media_id",
        "observation_type",
        "scientific_name",
        "vernacular_name",
        "taxon_id",
        "count",
        "life_stage",
        "sex",
        "behavior",
        "classification_method",
        "classification_probability",
        "review_status",
        "source_type",
        "ai_origin",
        "deleted_at",
        "created_at",
    },
    "lorawan_messages": {"id", "device_eui", "device_id", "deployment_id", "raw_payload", "received_at"},
    "lorawan_parsed_messages": {"id", "lorawan_message_id", "device_id", "battery_level", "sd_card_used_capacity", "model_output"},
    "api_keys": {"id", "organisation_id", "name", "key_hash", "key_prefix", "scopes", "expires_at", "last_used_at", "revoked_at", "created_at"},
    "api_jobs": {"id", "status", "job_data", "created_at", "updated_at"},
    "user_roles": {"id", "user_id", "role", "scope_type", "scope_id", "expires_at", "is_active", "deleted_at", "created_at"},
}

# (table, embed) -> (embedded table, column on table, column on embedded table, to-one)
RELATIONS = {
    ("deployments", "projects"): ("projects", "project_id", "id", True),
    ("deployments", "devices"): ("devices", "device_id", "id", True),
    ("deployments", "deployment_statuses"): ("deployment_statuses", "deployment_status_id", "id", True),
    ("media", "deployments"): ("deployments", "deployment_id", "id", True),
    ("media", "observations"): ("observations", "id", "media_id", False),
    ("lorawan_messages", "deployments"): ("deployments", "deployment_id", "id", True),
    ("lorawan_messages", "lorawan_parsed_messages"): ("lorawan_parsed_messages", "id", "lorawan_message_id", False),
}

_EMBED = re.compile(r"^(?P<name>\w+)(?P<inner>!inner)?\((?P<inner_select>.*)\)$", re.S)


def _split(select: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in select:
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
            continue
        depth += ch == "("
        depth -= ch == ")"
        cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def _parse(table: str, select: str) -> dict:
    tree: dict = {"star": False, "cols": [], "embeds": {}}
    for part in _split(select):
        m = _EMBED.match(part)
        if m:
            name = m["name"]
            assert (table, name) in RELATIONS, f"no relation {table} -> {name}"
            child = RELATIONS[(table, name)][0]
            tree["embeds"][name] = (bool(m["inner"]), _parse(child, m["inner_select"]))
        elif part == "*":
            tree["star"] = True
        else:
            _check_column(table, part)
            tree["cols"].append(part)
    return tree


def _check_column(table: str, column: str) -> None:
    assert column in COLUMNS[table], f"{table}.{column} is not in ww-backend's schema"


class Query:
    def __init__(self, db: "FakeDB", table: str):
        self.db, self.table = db, table
        self.tree: Optional[dict] = None
        self.count = False
        self.filters: list[tuple[str, str, Any]] = []
        self.orders: dict[str, list[tuple[str, bool, Optional[bool]]]] = {}
        self.offset, self.limit_n = 0, None
        self.mode, self.payload = "select", None

    # ── builders ──
    def select(self, columns: str = "*", count: Optional[str] = None):
        self.tree = _parse(self.table, columns)
        self.count = count == "exact"
        return self

    def update(self, payload: dict):
        self.mode, self.payload = "update", payload
        return self

    def upsert(self, payload: dict):
        self.mode, self.payload = "upsert", payload
        return self

    def _filter(self, path: str, op: str, value: Any):
        self.filters.append((path, op, value))
        return self

    def eq(self, path, value):
        return self._filter(path, "eq", value)

    def is_(self, path, value):
        assert value == "null"
        return self._filter(path, "is", None)

    def in_(self, path, values):
        return self._filter(path, "in", list(values))

    def gte(self, path, value):
        return self._filter(path, "gte", value)

    def lte(self, path, value):
        return self._filter(path, "lte", value)

    def gt(self, path, value):
        return self._filter(path, "gt", value)

    def order(self, column, *, desc=False, nullsfirst=None, foreign_table=None):
        self.orders.setdefault(foreign_table or "", []).append((column, desc, nullsfirst))
        return self

    def range(self, start, end):
        self.offset, self.limit_n = start, end - start + 1
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    # ── evaluation ──
    def _filters_at(self, prefix: str) -> list[tuple[str, str, Any]]:
        """Filters on columns of the resource at ``prefix`` ('' for the table itself)."""
        out = []
        for path, op, value in self.filters:
            if not path.startswith(prefix):
                continue
            rest = path[len(prefix) :]
            if "." not in rest:
                out.append((rest, op, value))
        return out

    def _sort(self, rows: list[dict], key: str) -> list[dict]:
        for column, desc, nullsfirst in reversed(self.orders.get(key, [])):
            present = [r for r in rows if r.get(column) is not None]
            missing = [r for r in rows if r.get(column) is None]
            present.sort(key=lambda r: r[column], reverse=desc)
            first = desc if nullsfirst is None else nullsfirst  # PostgreSQL: NULLs sort as largest
            rows = missing + present if first else present + missing
        return rows

    def _materialize(self, table: str, row: dict, tree: dict, prefix: str) -> Optional[dict]:
        out = dict(row) if tree["star"] else {c: row.get(c) for c in tree["cols"]}
        for name, (inner, sub) in tree["embeds"].items():
            child_table, local, remote, to_one = RELATIONS[(table, name)]
            sub_prefix = f"{prefix}{name}."
            children = []
            for child in self.db.rows(child_table):
                if child.get(remote) != row.get(local) or row.get(local) is None:
                    continue
                if not all(_test(child, child_table, c, op, v) for c, op, v in self._filters_at(sub_prefix)):
                    continue
                built = self._materialize(child_table, child, sub, sub_prefix)
                if built is not None:
                    children.append(built)
            children = self._sort(children, name)
            if inner and not children:
                return None
            out[name] = (children[0] if children else None) if to_one else children
        return out

    def execute(self):
        if self.mode == "upsert":
            rows = self.db.tables.setdefault(self.table, [])
            existing = next((r for r in rows if r["id"] == self.payload["id"]), None)
            if existing:
                existing.update(copy.deepcopy(self.payload))
            else:
                rows.append(copy.deepcopy(self.payload))
            return SimpleNamespace(data=[self.payload], count=None)
        if self.mode == "update":
            hit = [r for r in self.db.rows(self.table) if all(_test(r, self.table, c, op, v) for c, op, v in self._filters_at(""))]
            for r in hit:
                r.update(self.payload)
            return SimpleNamespace(data=copy.deepcopy(hit), count=None)

        tree = self.tree or _parse(self.table, "*")
        for path, _, _ in self.filters:
            node, table = tree, self.table
            *embeds, column = path.split(".")
            for name in embeds:  # PostgREST refuses a filter on a resource the select does not embed
                assert name in node["embeds"], f"filter {path} names {name}, which the select does not embed"
                table, node = RELATIONS[(table, name)][0], node["embeds"][name][1]
            if "->>" not in column:
                _check_column(table, column)
        rows = []
        for row in self.db.rows(self.table):
            if not all(_test(row, self.table, c, op, v) for c, op, v in self._filters_at("")):
                continue
            built = self._materialize(self.table, row, tree, "")
            if built is None:
                continue
            built["__row"] = row
            rows.append(built)
        for column, _, _ in self.orders.get("", []):
            _check_column(self.table, column)
        # Sort on the stored row so a column that is ordered on but not selected still counts.
        keyed = self._sort([{**r["__row"], "__built": r} for r in rows], "")
        rows = [k["__built"] for k in keyed]
        total = len(rows)
        end = None if self.limit_n is None else self.offset + self.limit_n
        page = rows[self.offset : end]
        self.db.requests.append(SimpleNamespace(table=self.table, filters=list(self.filters), returned=len(page)))
        for r in page:
            r.pop("__row", None)
        return SimpleNamespace(data=copy.deepcopy(page), count=total if self.count else None)


def _test(row: dict, table: str, column: str, op: str, value: Any) -> bool:
    if "->>" in column:  # job_data->>user_id
        col, key = column.split("->>")
        actual = (row.get(col) or {}).get(key)
    else:
        _check_column(table, column)
        actual = row.get(column)
    if op == "eq":
        return actual is not None and str(actual) == str(value)
    if op == "is":
        return actual is None
    if op == "in":
        return actual in value
    if actual is None:
        return False
    return {"gte": actual >= value, "lte": actual <= value, "gt": actual > value}[op]


class FakeDB:
    """``create_service_client()`` for tests: ``FakeDB(tables).client``."""

    def __init__(self, tables: dict[str, list[dict]]):
        self.tables = {name: [dict(r) for r in rows] for name, rows in tables.items()}
        self.requests: list[SimpleNamespace] = []

    def rows(self, table: str) -> list[dict]:
        return self.tables.setdefault(table, [])

    def table(self, name: str) -> Query:
        assert name in COLUMNS, f"unknown table {name}"
        return Query(self, name)

    def client(self) -> "FakeDB":
        return self
