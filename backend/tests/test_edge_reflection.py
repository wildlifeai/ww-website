"""Unit tests for the edge-reflection builder (lifecycle stage 8, pure parts)."""

from app.domain.edge_reflection import (
    DEFAULT_REFLECT_THRESHOLD_PCT,
    build_edge_observations,
    edge_model_version,
)

MODEL = {
    "id": "11111111-1111-1111-1111-111111111111",
    "name": "Rat Detection",
    "version_number": 10,
    "ai_model_families": {"firmware_model_id": 42},
    "label_map": {
        "rat": {
            "role": "target",
            "taxon_id": "22222222-2222-2222-2222-222222222222",
            "scientific_name": "Rattus rattus",
            "vernacular_name": "Ship rat",
            "threshold": 80,
        },
        "not rat": {"role": "background", "taxon_id": None},
    },
}


def _media(fields):
    return {"id": "33333333-3333-3333-3333-333333333333", "exif_metadata": {"user_comment_fields": fields}}


def test_edge_model_version_matches_tfl_filename():
    assert edge_model_version(MODEL) == "42V10"
    assert edge_model_version({"version_number": 1}) is None


def test_target_above_threshold_becomes_edge_observation():
    rows = build_edge_observations(_media({"rat": "87%", "not rat": "13%"}), "dep-1", MODEL, "2026-07-08T00:00:00Z")
    assert len(rows) == 1
    row = rows[0]
    assert row["ai_origin"] == "edge"
    assert row["source_type"] == "ai"
    assert row["source_model_version"] == "42V10"
    assert row["source_model_id"] == MODEL["id"]
    assert row["scientific_name"] == "Rattus rattus"
    assert row["taxon_id"] == MODEL["label_map"]["rat"]["taxon_id"]
    assert row["classification_probability"] == 0.87
    assert row["observation_type"] == "animal"
    assert "bbox_x" not in row  # whole-image classifier: no bbox quad


def test_background_and_subthreshold_labels_are_skipped():
    # 'not rat' is background; 'rat' at 12 is below its 80 threshold.
    assert build_edge_observations(_media({"rat": "12", "not rat": "88"}), "dep-1", MODEL, "t") == []


def test_percent_suffix_and_bare_numbers_both_parse():
    with_pct = build_edge_observations(_media({"rat": "90%"}), "dep-1", MODEL, "t")
    bare = build_edge_observations(_media({"rat": "90"}), "dep-1", MODEL, "t")
    assert len(with_pct) == len(bare) == 1
    assert with_pct[0]["classification_probability"] == bare[0]["classification_probability"] == 0.9


def test_telemetry_and_unmapped_labels_are_ignored():
    # Device telemetry (Batt/Temp) and labels missing from label_map must not
    # become observations; non-numeric values never crash the builder.
    rows = build_edge_observations(
        _media({"Batt": "87", "Temp": "14.5", "possum": "99", "rat": "abc"}),
        "dep-1",
        MODEL,
        "t",
    )
    assert rows == []


def test_default_threshold_applies_when_label_map_has_none():
    model = {
        **MODEL,
        "label_map": {"rat": {"role": "target", "taxon_id": None, "scientific_name": "Rattus rattus"}},
    }
    below = build_edge_observations(_media({"rat": str(DEFAULT_REFLECT_THRESHOLD_PCT - 1)}), "d", model, "t")
    at = build_edge_observations(_media({"rat": str(DEFAULT_REFLECT_THRESHOLD_PCT)}), "d", model, "t")
    assert below == []
    assert len(at) == 1


PERSON_MODEL = {
    **MODEL,
    "name": "Person Detection",
    "ai_model_families": {"firmware_model_id": 20},
    "version_number": 1,
    "label_map": {
        "no person": {"role": "background"},
        "person": {"role": "target", "predicts": "type", "observation_type": "human"},
    },
}


def test_taxon_class_writes_an_animal_with_its_taxon():
    model = {**MODEL, "label_map": {**MODEL["label_map"], "rat": {**MODEL["label_map"]["rat"], "predicts": "taxon"}}}
    (row,) = build_edge_observations(_media({"rat": "90%"}), "dep-1", model, "t")
    assert row["observation_type"] == "animal"
    assert row["taxon_id"] == "22222222-2222-2222-2222-222222222222"
    assert row["scientific_name"] == "Rattus rattus"
    assert row["vernacular_name"] == "Ship rat"


def test_type_person_class_writes_a_human_not_an_animal():
    """#135: the camera's person detections were typed animal."""
    (row,) = build_edge_observations(_media({"person": "91%", "no person": "9%"}), "dep-1", PERSON_MODEL, "t")
    assert row["observation_type"] == "human"
    assert row["taxon_id"] is None and row["scientific_name"] is None and row["vernacular_name"] is None
    assert row["ai_origin"] == "edge"
    assert row["source_model_version"] == "20V1"
    assert row["classification_probability"] == 0.91


def test_type_class_with_empty_or_blank_type_writes_nothing():
    for obs_type in (None, "", "  ", "blank", "unknown"):
        model = {**PERSON_MODEL, "label_map": {"person": {"role": "target", "predicts": "type", "observation_type": obs_type}}}
        assert build_edge_observations(_media({"person": "95%"}), "dep-1", model, "t") == [], obs_type


def test_behaviour_class_writes_nothing():
    model = {**MODEL, "label_map": {"grooming": {"role": "target", "predicts": "behavior", "behavior": "grooming"}}}
    assert build_edge_observations(_media({"grooming": "99%"}), "dep-1", model, "t") == []


def test_label_the_label_map_does_not_know_writes_nothing():
    assert build_edge_observations(_media({"cat": "99%"}), "dep-1", PERSON_MODEL, "t") == []


def test_mixed_model_types_each_class_by_what_it_predicts():
    model = {
        **MODEL,
        "label_map": {
            "rat": {"role": "target", "predicts": "taxon", "scientific_name": "Rattus rattus"},
            "person": {"role": "target", "predicts": "type", "observation_type": "human"},
            "other": {"role": "background"},
        },
    }
    rows = build_edge_observations(_media({"rat": "60%", "person": "70%", "other": "5%"}), "dep-1", model, "t")
    assert sorted((r["observation_type"], r["scientific_name"]) for r in rows) == [("animal", "Rattus rattus"), ("human", None)]


def test_no_user_comment_fields_yields_no_rows():
    assert build_edge_observations({"id": "m", "exif_metadata": None}, "d", MODEL, "t") == []
    assert build_edge_observations({"id": "m", "exif_metadata": {}}, "d", MODEL, "t") == []


def test_camera_ai_rows_do_not_count_as_cloud_annotated():
    """The upload job reflects before the pipeline; an edge row must not make it skip the frame (#161)."""
    from app.domain.pipeline import cloud_annotated_media_ids

    rows = [
        {"media_id": "flagged-by-camera", "ai_origin": "edge"},
        {"media_id": "done-by-cloud", "ai_origin": "cloud"},
        {"media_id": "done-before-ai-origin", "ai_origin": None},
    ]
    assert cloud_annotated_media_ids(rows) == {"done-by-cloud", "done-before-ai-origin"}


async def test_auto_annotate_reflects_the_camera_before_the_pipeline(monkeypatch):
    """So the Camera AI result exists when the cloud steps run (#161)."""
    from app.domain import edge_reflection, pipeline
    from app.jobs import definitions

    calls: list[str] = []

    async def reflect(dep_id):
        calls.append("reflect")
        return 1

    async def run(**kwargs):
        calls.append("pipeline")

    async def after(*args, **kwargs):
        calls.append("after")

    monkeypatch.setattr(definitions, "build_pipeline_steps", lambda: [pipeline.PipelineStepType.SPECIESNET])
    monkeypatch.setattr(edge_reflection, "reflect_edge_deployment", reflect)
    monkeypatch.setattr(pipeline, "run_pipeline", run)
    monkeypatch.setattr(definitions, "emit_detection_notifications", after)
    monkeypatch.setattr(definitions, "auto_embed_deployment", after)

    await definitions.auto_annotate_deployments(["44444444-4444-4444-4444-444444444444"])
    assert calls == ["reflect", "pipeline", "after", "after"]
