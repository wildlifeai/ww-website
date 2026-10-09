# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Upload dedup sees every media row, not just PostgREST's first 1,000 (#317)."""

from app.jobs.definitions import _DEDUP_CHUNK, unregistered_uploads

DEP = "00000000-0000-0000-0000-0000000000d1"
OTHER = "00000000-0000-0000-0000-0000000000d2"
CAP = 1000  # PostgREST's response cap


class _Query:
    def __init__(self, client):
        self.client = client
        self.filters: list = []

    def select(self, _cols):
        return self

    def eq(self, col, value):
        self.filters.append(lambda r: r.get(col) == value)
        self.client.calls[-1]["eq"][col] = value
        return self

    def in_(self, col, values):
        values = list(values)
        self.filters.append(lambda r: r.get(col) in values)
        self.client.calls[-1]["in"] = (col, values)
        return self

    def is_(self, col, value):
        assert value == "null"
        self.filters.append(lambda r: r.get(col) is None)
        return self

    def execute(self):
        if self.client.fail:
            raise RuntimeError("boom")
        rows = [r for r in self.client.rows if all(f(r) for f in self.filters)]
        return type("Resp", (), {"data": rows[:CAP]})()


class _Client:
    """Fake service client over an in-memory media table that caps reads at 1,000 rows."""

    def __init__(self, rows: list[dict], fail: bool = False):
        self.rows = rows
        self.fail = fail
        self.calls: list[dict] = []

    def table(self, name):
        assert name == "media"
        self.calls.append({"eq": {}, "in": None})
        return _Query(self)


def _row(i: int, dep: str = DEP, hashed: bool = True, **extra) -> dict:
    return {"deployment_id": dep, "file_hash": f"{i:064x}" if hashed else None, "file_path": f"gdrive://f{i}", **extra}


def _upload(i: int, dep: str = DEP, file_hash: str | None = None) -> dict:
    return {"deployment_id": dep, "file_id": f"f{i}", "file_hash": file_hash or f"{i:064x}"}


def test_a_reupload_past_the_first_1000_rows_is_not_registered_again():
    svc = _Client([_row(i) for i in range(1500)])
    # The old full read only ever saw the first 1,000 rows.
    seen = svc.table("media").select("file_hash, file_path").eq("deployment_id", DEP).execute().data
    assert len(seen) == CAP and _row(1400) not in seen

    assert unregistered_uploads(svc, [_upload(1400)]) == []


def test_hashless_rows_are_matched_by_path():
    svc = _Client([_row(i, hashed=False) for i in range(1200)])
    known = _upload(1100, file_hash="a" * 64)  # a back-filled row: same Drive file, no hash
    new = _upload(5000)

    assert unregistered_uploads(svc, [known, new]) == [new]


def test_soft_deleted_rows_still_count_as_registered():
    svc = _Client([_row(7, deleted_at="2026-10-01T00:00:00+00:00")])

    assert unregistered_uploads(svc, [_upload(7)]) == []


def test_lookups_are_chunked_and_scoped_to_the_deployment():
    svc = _Client([_row(3, dep=OTHER)])
    uploads = [_upload(i) for i in range(120)]

    # The same Drive file in another deployment does not make this one a duplicate.
    assert unregistered_uploads(svc, uploads) == uploads
    assert all(c["eq"] == {"deployment_id": DEP} for c in svc.calls)
    assert all(len(c["in"][1]) <= _DEDUP_CHUNK for c in svc.calls)
    # 120 hashes and 120 paths, 50 at a time.
    assert sorted(c["in"][0] for c in svc.calls) == ["file_hash"] * 3 + ["file_path"] * 3


def test_a_photo_twice_in_one_batch_is_registered_once():
    svc = _Client([])
    first = _upload(1)
    same_hash = {**_upload(2), "file_hash": first["file_hash"]}  # same photo, another Drive file
    same_path = {**_upload(1), "file_hash": None}  # hash-less, same Drive file
    other_dep = _upload(1, dep=OTHER)  # the same Drive file in another deployment is its own row
    new = _upload(3)

    assert unregistered_uploads(svc, [first, same_hash, same_path, other_dep, new]) == [first, other_dep, new]


def test_a_failed_lookup_registers_rather_than_strands_the_upload():
    svc = _Client([_row(1)], fail=True)

    assert unregistered_uploads(svc, [_upload(1)]) == [_upload(1)]
