# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""CamtrapDP import: observation rows carry only real ``observations`` columns (#138)."""

from app.domain.camtrapdp import _insert_observations, _observation_row

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
