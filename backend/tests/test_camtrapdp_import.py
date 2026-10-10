# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""CamtrapDP import: observation rows carry only real ``observations`` columns (#138), and a
camera's second open deployment does not abort the import (ww-backend #320)."""

import uuid

import pytest
from postgrest.exceptions import APIError

from app.domain.camtrapdp import CamtrapPackage, _insert_observations, _observation_row, import_package

# Every column _observation_row may write, checked against ww-backend 35_observations.sql.
OBSERVATION_COLUMNS = {
    "id", "deployment_id", "media_id", "observation_event_id", "observation_level", "observation_type",
    "scientific_name", "source_type", "review_status", "count", "life_stage", "sex", "behavior",
    "individual_id", "classification_method", "classified_by", "classification_probability", "confidence",
    "observation_comments", "bbox_x", "bbox_y", "bbox_w", "bbox_h",
}  # fmt: skip

FULL_OBSERVATION = {
    "observationID": "obs1",
    "deploymentID": "dep1",
    "mediaID": "med1",
    "eventID": "ev1",
    "observationLevel": "media",
    "observationType": "animal",
    "scientificName": "Rattus rattus",
    "taxonID": "2439261",  # a bare GBIF key: the case that failed the whole batch
    "count": "2",
    "lifeStage": "adult",
    "sex": "female",
    "behavior": "foraging",
    "individualID": "r7",
    "classificationMethod": "human",
    "classifiedBy": "Tommy",
    "classificationProbability": "0.9",
    "observationComments": "near the bait",
    "bboxX": "0.1",
    "bboxY": "0.2",
    "bboxWidth": "0.3",
    "bboxHeight": "0.4",
}


def test_a_numeric_taxon_id_writes_no_extra_column():
    row = _observation_row(FULL_OBSERVATION, "ww-dep", "ww-med", "ww-ev", "final")

    assert "gbif_taxon_key" not in row
    assert set(row) == OBSERVATION_COLUMNS
    assert row["count"] == 2 and row["bbox_w"] == 0.3 and row["media_id"] == "ww-med"


def test_values_outside_the_schema_enums_are_coerced_or_dropped():
    o = {"observationType": "bird", "lifeStage": "egg", "sex": "both", "classificationMethod": "crowd"}

    row = _observation_row(o, "ww-dep", None, None, "final")

    assert row["observation_type"] == "unknown"
    assert row["observation_level"] == "event"  # no media
    assert not {"life_stage", "sex", "classification_method", "media_id"} & set(row)


class _Table:
    def __init__(self, error=None):
        self.error, self.inserted = error, []

    def insert(self, rows):
        self.inserted.append(rows)
        return self

    def execute(self):
        if self.error:
            raise self.error


class _Svc:
    def __init__(self, table):
        self._table = table

    def table(self, name):
        assert name == "observations"
        return self._table


def test_a_batch_inserts_and_counts():
    warnings: list[str] = []
    table = _Table()

    assert _insert_observations(_Svc(table), [{"id": "a"}, {"id": "b"}], warnings) == 2
    assert warnings == []


def test_a_failed_batch_says_how_many_rows_were_lost():
    warnings: list[str] = []
    table = _Table(error=RuntimeError('column "x" of relation "observations" does not exist'))

    assert _insert_observations(_Svc(table), [{"id": "a"}] * 3, warnings) == 0
    assert warnings == ['3 observations were not imported: column "x" of relation "observations" does not exist']


# ── Deployments: one open deployment per camera (ww-backend #320) ───────────
#
# A fake service client over in-memory tables. Its deployments insert enforces
# deployments_one_open_per_device as the database does at commit: 23P01 with the device id.

ORG = "org-1"


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.filters, self.rows = db, name, [], None

    def select(self, _cols):
        return self

    def eq(self, col, val):
        self.filters.append(lambda r: r.get(col) == val)
        return self

    def in_(self, col, vals):
        self.filters.append(lambda r: r.get(col) in vals)
        return self

    def limit(self, _n):
        return self

    def insert(self, rows):
        self.rows = rows if isinstance(rows, list) else [rows]
        return self

    def execute(self):
        table = self.db.tables.setdefault(self.name, [])
        if self.rows is None:
            return _Result([r for r in table if all(f(r) for f in self.filters)])
        for row in self.rows:
            if self.name == "deployments":
                if self.db.deployment_error:
                    raise self.db.deployment_error
                open_on_device = [d for d in table if d["device_id"] == row["device_id"] and not d.get("deployment_end")]
                if not row.get("deployment_end") and open_on_device:
                    did = row["device_id"]
                    raise APIError({"code": "23P01", "message": "conflicting key value", "details": f"Key (device_id)=({did}) conflicts"})
            table.append(dict(row))
        return _Result(self.rows)


class _Result:
    def __init__(self, data):
        self.data = data


class _Db:
    def __init__(self, devices=(), deployment_error=None):
        self.tables = {"devices": [dict(d) for d in devices]}
        self.deployment_error = deployment_error

    def table(self, name):
        return _Q(self, name)

    def rows(self, name):
        return self.tables.get(name, [])


def _pkg(*deps):
    deployments = [{"deploymentID": d, "cameraID": cam, "deploymentStart": start, "deploymentEnd": end} for d, cam, start, end in deps]
    media = [{"mediaID": f"m-{d}", "deploymentID": d, "filePath": f"https://x/{d}.jpg"} for d, *_ in deps]
    return CamtrapPackage(metadata={"title": "t"}, deployments=deployments, media=media)


def _import(db, pkg):
    return import_package(pkg, "user-1", ORG, db, annotation_mode="unprocessed")


def test_a_second_open_deployment_on_a_camera_gets_its_own_device():
    db = _Db()
    pkg = _pkg(("d1", "CAM1", "2026-01-01T00:00:00Z", ""), ("d2", "CAM1", "2026-03-01T00:00:00Z", ""))

    result = _import(db, pkg)

    assert result.deployments_imported == 2 and result.media_imported == 2
    assert sorted(d["name"] for d in db.rows("devices")) == ["[imported] CAM1", "[imported] CAM1 (d2)"]
    deps = {d["deployment_start"]: d for d in db.rows("deployments")}
    assert deps["2026-01-01T00:00:00Z"]["device_id"] != deps["2026-03-01T00:00:00Z"]["device_id"]
    media_dep = {m["file_path"]: m["deployment_id"] for m in db.rows("media")}
    assert media_dep["https://x/d2.jpg"] == deps["2026-03-01T00:00:00Z"]["id"]
    assert any("'CAM1'" in w and "'d2'" in w and "[imported] CAM1 (d2)" in w for w in result.warnings)


def test_a_reimport_reuses_both_devices_and_deployments():
    db = _Db()
    pkg = _pkg(("d1", "CAM1", "2026-01-01T00:00:00Z", ""), ("d2", "CAM1", "2026-03-01T00:00:00Z", ""))
    _import(db, pkg)

    result = _import(db, pkg)

    assert result.deployments_imported == 2
    assert len(db.rows("devices")) == 2 and len(db.rows("deployments")) == 2
    assert len({m["deployment_id"] for m in db.rows("media")}) == 2


def test_a_closed_deployment_shares_the_camera_device():
    db = _Db()
    pkg = _pkg(("d1", "CAM1", "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"), ("d2", "CAM1", "2026-03-01T00:00:00Z", ""))

    result = _import(db, pkg)

    assert len(db.rows("devices")) == 1 and result.warnings == []


def test_any_other_deployment_error_still_fails_the_import():
    db = _Db(deployment_error=APIError({"code": "23502", "message": "null value"}))

    with pytest.raises(RuntimeError, match="Failed to insert deployment 'd1'"):
        _import(db, _pkg(("d1", "CAM1", "2026-01-01T00:00:00Z", "")))


def _legacy_device(org):
    name = "[imported] CAM1"
    return {"id": f"dev-{org}", "name": name, "organisation_id": org, "bluetooth_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"imported-{name}"))}


def test_another_organisations_camera_of_the_same_name_is_not_reused():
    db = _Db(devices=[_legacy_device("org-2")])

    _import(db, _pkg(("d1", "CAM1", "2026-01-01T00:00:00Z", "")))

    (dep,) = db.rows("deployments")
    device = next(d for d in db.rows("devices") if d["id"] == dep["device_id"])
    assert device["organisation_id"] == ORG and device["id"] != "dev-org-2"


def test_a_device_from_before_the_organisation_seed_is_reused_by_its_own_organisation():
    db = _Db(devices=[_legacy_device(ORG)])

    _import(db, _pkg(("d1", "CAM1", "2026-01-01T00:00:00Z", "")))

    assert len(db.rows("devices")) == 1 and db.rows("deployments")[0]["device_id"] == f"dev-{ORG}"
