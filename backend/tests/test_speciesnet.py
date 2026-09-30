# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the SpeciesNet parsing + observation-mapping (pure logic).

Exercises the SpeciesNet output parser, taxonomy-string parsing, and the
domain mapping to CamtrapDP observation rows — all without the heavy
``speciesnet`` package, a GPU, or a database.
"""

from app.domain.pipeline import (
    build_crop_classification_observation,
    build_speciesnet_observations,
    rollup_taxon,
    run_pipeline,
)
from app.jobs.definitions import _is_uuid
from app.services.bioclip_service import CropPrediction
from app.services.speciesnet_service import (
    Detection,
    ImagePrediction,
    category_to_observation_type,
    parse_prediction_string,
    parse_speciesnet_output,
    parse_taxonomy,
)

_RAW = {
    "predictions": [
        {
            "filepath": "/tmp/a.jpg",
            "detections": [
                {"category": "1", "label": "animal", "conf": 0.93, "bbox": [0.1, 0.2, 0.3, 0.4]},
                {"category": "1", "label": "animal", "conf": 0.10, "bbox": [0.5, 0.5, 0.1, 0.1]},
            ],
            "prediction": "12345678-1234-1234-1234-1234567890ab;mammalia;apterygiformes;apterygidae;apteryx;mantelli;north island brown kiwi",
            "prediction_score": 0.87,
        },
        {
            "filepath": "/tmp/blank.jpg",
            "detections": [],
            "prediction": "",
            "prediction_score": None,
        },
    ]
}


def test_category_to_observation_type():
    assert category_to_observation_type("1") == "animal"
    assert category_to_observation_type("2") == "human"
    assert category_to_observation_type("3") == "vehicle"
    assert category_to_observation_type("banana") == "unknown"


def test_parse_prediction_string_extracts_scientific_and_common():
    sci, common = parse_prediction_string(
        "12345678-1234-1234-1234-1234567890ab;mammalia;apterygiformes;apterygidae;apteryx;mantelli;north island brown kiwi"
    )
    assert sci == "Apteryx mantelli"
    assert common == "north island brown kiwi"


def test_parse_prediction_string_empty():
    assert parse_prediction_string("") == (None, None)
    assert parse_prediction_string(None) == (None, None)


def test_parse_prediction_string_drops_no_cv_result_sentinel():
    """SpeciesNet's 'no cv result' (unclassifiable detection) must not surface as a species
    name (was showing 'No cv result no cv result')."""
    uuid = "12345678-1234-1234-1234-123456789abc"
    assert parse_prediction_string(f"{uuid};no cv result") == (None, None)
    assert parse_prediction_string(f"{uuid};no cv result;no cv result") == (None, None)
    # A real classification alongside is unaffected.
    sci, common = parse_prediction_string(f"{uuid};aves;;;apteryx;mantelli;north island brown kiwi")
    assert sci == "Apteryx mantelli"


def test_parse_speciesnet_output_shapes():
    preds = parse_speciesnet_output(_RAW)
    assert len(preds) == 2
    a, blank = preds
    assert a.filepath == "/tmp/a.jpg"
    assert len(a.detections) == 2
    assert a.detections[0].observation_type == "animal"
    assert a.detections[0].bbox == (0.1, 0.2, 0.3, 0.4)
    assert a.scientific_name == "Apteryx mantelli"
    assert a.classification_score == 0.87
    assert not a.is_blank
    assert blank.is_blank


def test_build_observations_filters_by_threshold_and_sets_bbox():
    preds = parse_speciesnet_output(_RAW)
    rows = build_speciesnet_observations({"id": "media-1"}, "dep-1", preds[0], "speciesnet-vX", "2026-06-06T00:00:00Z", confidence_threshold=0.2)
    # Only the 0.93 detection survives the 0.2 threshold.
    assert len(rows) == 1
    r = rows[0]
    assert r["deployment_id"] == "dep-1"
    assert r["media_id"] == "media-1"
    assert r["observation_level"] == "media"
    assert r["observation_type"] == "animal"
    assert r["source_type"] == "ai"
    assert r["ai_origin"] == "cloud"
    assert r["review_status"] == "ai_reviewed"
    assert r["confidence"] == 0.93
    assert r["scientific_name"] == "Apteryx mantelli"
    assert r["vernacular_name"] == "north island brown kiwi"
    assert (r["bbox_x"], r["bbox_y"], r["bbox_w"], r["bbox_h"]) == (0.1, 0.2, 0.3, 0.4)
    assert "id" in r


def test_build_observations_blank_when_all_below_threshold():
    preds = parse_speciesnet_output(_RAW)
    rows = build_speciesnet_observations({"id": "media-1"}, "dep-1", preds[0], "speciesnet-vX", "2026-06-06T00:00:00Z", confidence_threshold=0.99)
    assert len(rows) == 1
    assert rows[0]["observation_type"] == "blank"
    assert rows[0]["ai_origin"] == "cloud"  # cloud provenance on blanks too
    assert "bbox_x" not in rows[0]  # blank rows carry no bbox (chk_bbox_complete)


def test_build_observations_blank_image():
    preds = parse_speciesnet_output(_RAW)
    rows = build_speciesnet_observations({"id": "media-2"}, "dep-1", preds[1], "speciesnet-vX", "2026-06-06T00:00:00Z")
    assert len(rows) == 1
    assert rows[0]["observation_type"] == "blank"


# ── Per-detection mode (FF_PER_CROP_CLASSIFY_ENABLED) ─────────────────────────


def _pred(*detections, sci="Turdus merula", common="Common Blackbird", score=0.7):
    return ImagePrediction(
        filepath="/tmp/x.jpg",
        detections=list(detections),
        scientific_name=sci,
        common_name=common,
        classification_score=score,
        taxonomy={},
    )


def _det(conf, bbox, obs_type="animal", category="1"):
    return Detection(category=category, observation_type=obs_type, confidence=conf, bbox=bbox)


def test_build_observations_collapse_is_the_default():
    # Two same-type boxes collapse into ONE row carrying count=N (legacy behaviour).
    pred = _pred(_det(0.9, (0.1, 0.1, 0.2, 0.2)), _det(0.8, (0.5, 0.5, 0.2, 0.2)))
    rows = build_speciesnet_observations({"id": "m"}, "dep", pred, "v", "2026-01-01T00:00:00Z")
    assert len(rows) == 1
    assert rows[0]["count"] == 2
    assert (rows[0]["bbox_x"], rows[0]["bbox_y"]) == (0.1, 0.1)  # highest-confidence box


def test_build_observations_per_detection_one_row_per_box():
    # Two of the same species → two observations, each count=1, distinct bbox,
    # both carrying the provisional image-level species.
    pred = _pred(_det(0.9, (0.1, 0.1, 0.2, 0.2)), _det(0.8, (0.5, 0.5, 0.2, 0.2)))
    rows = build_speciesnet_observations({"id": "m"}, "dep", pred, "v", "2026-01-01T00:00:00Z", per_detection=True)
    assert len(rows) == 2
    assert all(r["observation_type"] == "animal" and r["count"] == 1 for r in rows)
    assert {(r["bbox_x"], r["bbox_y"]) for r in rows} == {(0.1, 0.1), (0.5, 0.5)}
    assert all(r["scientific_name"] == "Turdus merula" for r in rows)
    assert len({r["id"] for r in rows}) == 2  # distinct observation ids
    assert all(r["confidence"] == det_conf for r, det_conf in zip(rows, (0.9, 0.8)))


def test_build_observations_per_detection_mixed_types_not_merged():
    # An animal + a human box stay as two separate observations in both modes,
    # but per-detection also keeps multiple animals separate.
    pred = _pred(
        _det(0.9, (0.1, 0.1, 0.2, 0.2), obs_type="animal"),
        _det(0.85, (0.3, 0.3, 0.2, 0.2), obs_type="animal"),
        _det(0.7, (0.6, 0.6, 0.2, 0.2), obs_type="human", category="2"),
    )
    rows = build_speciesnet_observations({"id": "m"}, "dep", pred, "v", "2026-01-01T00:00:00Z", per_detection=True)
    assert len(rows) == 3
    assert sum(r["observation_type"] == "animal" for r in rows) == 2
    assert sum(r["observation_type"] == "human" for r in rows) == 1
    # only animal rows carry a species
    assert all("scientific_name" not in r for r in rows if r["observation_type"] == "human")


def test_build_observations_per_detection_threshold_and_blank():
    pred = _pred(_det(0.9, (0.1, 0.1, 0.2, 0.2)), _det(0.1, (0.5, 0.5, 0.2, 0.2)))
    rows = build_speciesnet_observations({"id": "m"}, "dep", pred, "v", "2026-01-01T00:00:00Z", confidence_threshold=0.5, per_detection=True)
    assert len(rows) == 1  # only the 0.9 box survives
    # all-below-threshold → single blank, no bbox
    blank = build_speciesnet_observations({"id": "m"}, "dep", pred, "v", "2026-01-01T00:00:00Z", confidence_threshold=0.99, per_detection=True)
    assert len(blank) == 1 and blank[0]["observation_type"] == "blank" and "bbox_x" not in blank[0]


# ── Phase 3: per-crop classification (build_crop_classification_observation) ──


def _crop(sci="Felis catus", common="Domestic Cat", score=0.8):
    return CropPrediction(filepath="/tmp/c.jpg", scientific_name=sci, common_name=common, score=score, rank="species")


_DETECTION = {
    "id": "speciesnet-row",
    "media_id": "m1",
    "crop_url": "crops/dep/m1/speciesnet-row.jpg",
    "confidence": 0.91,
    "bbox_x": 0.1,
    "bbox_y": 0.2,
    "bbox_w": 0.3,
    "bbox_h": 0.4,
}


def test_crop_classification_is_its_own_row_on_the_detection_box():
    """#162: the classifier writes beside the SpeciesNet row, never onto it."""
    row = build_crop_classification_observation(_DETECTION, "dep", _crop(), "bioclip-v1", "2026-01-01T00:00:00Z")
    assert row["id"] != _DETECTION["id"]
    assert (row["media_id"], row["deployment_id"]) == ("m1", "dep")
    assert (row["scientific_name"], row["vernacular_name"]) == ("Felis catus", "Domestic Cat")
    assert row["source_model_version"] == row["classified_by"] == "bioclip-v1"
    assert (row["source_type"], row["ai_origin"], row["review_status"]) == ("ai", "cloud", "ai_reviewed")
    assert row["classification_probability"] == 0.8
    # The detection's box and confidence link it to that detection; the crop stays the detector's.
    assert (row["bbox_x"], row["bbox_y"], row["bbox_w"], row["bbox_h"]) == (0.1, 0.2, 0.3, 0.4)
    assert row["confidence"] == 0.91
    assert "crop_url" not in row


def test_crop_classification_adds_nothing_without_a_name():
    assert build_crop_classification_observation(_DETECTION, "dep", _crop(sci=None), "bioclip-v1", "t") is None


def test_crop_classification_adds_nothing_below_threshold():
    assert build_crop_classification_observation(_DETECTION, "dep", _crop(score=0.2), "bioclip-v1", "t", confidence_threshold=0.5) is None


def test_crop_classification_allows_missing_score():
    # A None score is not "below threshold": the classifier just didn't report a probability.
    row = build_crop_classification_observation(_DETECTION, "dep", _crop(score=None), "bioclip-v1", "t", confidence_threshold=0.5)
    assert row is not None and row["classification_probability"] is None


def test_delete_superseded_ai_observations_scopes_and_cleans_crops():
    """Replace-on-rerun deletes only this model's machine rows for the given
    media (never human-reviewed ones) and removes their orphaned crops."""
    from unittest.mock import MagicMock

    from app.domain.pipeline import delete_superseded_ai_observations

    captured = {}
    table = MagicMock()
    table.select.return_value = table
    table.delete.return_value = table
    table.in_.side_effect = lambda col, vals: (captured.update(in_col=col, in_vals=vals), table)[1]
    table.eq.side_effect = lambda col, val: (captured.setdefault("eq", {}).update({col: val}), table)[1]
    # The doomed rows the select returns → drive crop-path construction.
    table.execute.return_value = MagicMock(
        data=[
            {"id": "o1", "media_id": "m1", "deployment_id": "dep1"},
            {"id": "o2", "media_id": "m2", "deployment_id": "dep1"},
        ]
    )
    svc = MagicMock()
    svc.table.return_value = table
    removed = {}
    svc.storage.from_.return_value.remove.side_effect = lambda paths: removed.update(paths=paths)

    delete_superseded_ai_observations(svc, ["m1", "m2"], "speciesnet-v4.0.1a")

    svc.table.assert_called_with("observations")
    table.delete.assert_called_once()
    assert captured["in_col"] == "media_id"
    assert captured["in_vals"] == ["m1", "m2"]
    # Scoped to this model version and only the unreviewed machine state.
    assert captured["eq"] == {"source_model_version": "speciesnet-v4.0.1a", "review_status": "ai_reviewed"}
    # Per-observation crops removed at their deterministic paths.
    assert removed["paths"] == ["crops/dep1/m1/o1.jpg", "crops/dep1/m2/o2.jpg"]


def test_delete_superseded_ai_observations_noop_on_empty():
    from unittest.mock import MagicMock

    from app.domain.pipeline import delete_superseded_ai_observations

    svc = MagicMock()
    delete_superseded_ai_observations(svc, [], "speciesnet-v4.0.1a")
    svc.table.assert_not_called()


# ── Taxonomy parsing + confidence-based roll-up (Tier 2 #5) ──────────────────


def test_parse_taxonomy_full_lineage():
    tax = parse_taxonomy("12345678-1234-1234-1234-1234567890ab;mammalia;apterygiformes;apterygidae;apteryx;mantelli;north island brown kiwi")
    assert tax["class"] == "mammalia"
    assert tax["family"] == "apterygidae"
    assert tax["genus"] == "apteryx"
    assert tax["species"] == "mantelli"
    assert tax["common"] == "north island brown kiwi"


def test_parse_taxonomy_keeps_hyphenated_first_field_without_uuid():
    # No UUID prefix: a hyphenated first field must NOT be mistaken for a UUID and dropped.
    tax = parse_taxonomy("actinopterygii-class;perciformes;;;;;ray-finned fish")
    assert tax["class"] == "actinopterygii-class"
    assert tax["order"] == "perciformes"


def test_parse_taxonomy_empty():
    assert parse_taxonomy("") == {}
    assert parse_taxonomy(None) == {}


_KIWI_TAX = {
    "class": "mammalia",
    "order": "apterygiformes",
    "family": "apterygidae",
    "genus": "apteryx",
    "species": "mantelli",
    "common": "north island brown kiwi",
}


def test_rollup_keeps_species_when_confident():
    sci, vern = rollup_taxon(_KIWI_TAX, 0.87)
    assert sci == "Apteryx mantelli"
    assert vern == "north island brown kiwi"


def test_rollup_backs_off_to_genus_on_mid_score():
    sci, vern = rollup_taxon(_KIWI_TAX, 0.40)
    assert sci == "Apteryx"
    assert vern is None  # no species-level common name claimed


def test_rollup_backs_off_to_family_on_low_score():
    sci, vern = rollup_taxon(_KIWI_TAX, 0.10)
    assert sci == "Apterygidae"
    assert vern is None


def test_rollup_empty_taxonomy_uses_fallback():
    sci, vern = rollup_taxon({}, 0.9, fallback_scientific="Felis catus", fallback_vernacular="cat")
    assert (sci, vern) == ("Felis catus", "cat")


# ── Detection dedup → one observation per type with count (Tier 2 #6) ────────


# ── Non-UUID deployment-id guard (unconfigured-camera "00000000" bug) ─────────


def test_is_uuid_accepts_real_uuid_rejects_folder_prefix():
    assert _is_uuid("7785fabb-e00e-4da2-aed6-a0fb906e6d79") is True
    assert _is_uuid("00000000") is False  # SD-card folder prefix, not a deployment
    assert _is_uuid("") is False
    assert _is_uuid(None) is False


async def test_run_pipeline_skips_non_uuid_deployment_without_db():
    # "00000000" (unconfigured camera) must NOT reach Postgres (would raise
    # 'invalid input syntax for type uuid'); the guard returns an empty result
    # before any DB access, so this runs with no Supabase client.
    result = await run_pipeline(deployment_id="00000000", steps=[])
    assert result.deployment_id == "00000000"
    assert result.total_observations == 0
    assert result.total_media == 0


def test_build_observations_dedups_animal_boxes_into_one_with_count():
    preds = parse_speciesnet_output(_RAW)
    # Threshold 0.0 keeps BOTH animal boxes (0.93 and 0.10).
    rows = build_speciesnet_observations({"id": "media-1"}, "dep-1", preds[0], "speciesnet-vX", "2026-06-06T00:00:00Z", confidence_threshold=0.0)
    # One animal observation, not two duplicates.
    assert len(rows) == 1
    r = rows[0]
    assert r["observation_type"] == "animal"
    assert r["count"] == 2
    # Representative bbox + confidence come from the highest-confidence box.
    assert r["confidence"] == 0.93
    assert (r["bbox_x"], r["bbox_y"], r["bbox_w"], r["bbox_h"]) == (0.1, 0.2, 0.3, 0.4)


async def test_per_crop_run_inserts_beside_speciesnet_and_never_updates_it(monkeypatch, tmp_path):
    """#162 acceptance: after a per-crop run the SpeciesNet row is untouched and a classifier row exists."""
    import time
    from unittest.mock import MagicMock

    from app.domain import media_resolver, pipeline

    svc = MagicMock()
    fetch = svc.table.return_value.select.return_value.in_.return_value.eq.return_value.eq.return_value.like.return_value.execute
    fetch.return_value.data = [_DETECTION]
    monkeypatch.setattr(pipeline, "create_service_client", lambda: svc)
    deleted = []
    monkeypatch.setattr(pipeline, "delete_superseded_ai_observations", lambda _svc, ids, version: deleted.append((set(ids), version)))

    async def resolve(url, size="full"):
        return b"jpeg", "image/jpeg"

    monkeypatch.setattr(media_resolver, "resolve_media", resolve)

    class Classifier:
        name = "bioclip"

        async def classify(self, paths, config):
            return [CropPrediction(filepath=p, scientific_name="Felis catus", common_name="Domestic Cat", score=0.8, rank="species") for p in paths]

    result = await pipeline.BioCLIPStep()._refine_crops_per_detection([{"id": "m1"}], "dep", {}, Classifier(), "bioclip-v1", 0.0, time.monotonic())

    svc.table.return_value.update.assert_not_called()
    inserted = svc.table.return_value.insert.call_args.args[0]
    assert [(r["media_id"], r["source_model_version"], r["bbox_x"]) for r in inserted] == [("m1", "bioclip-v1", 0.1)]
    assert deleted == [({"m1"}, "bioclip-v1")]
    assert result.observations_created == 1
