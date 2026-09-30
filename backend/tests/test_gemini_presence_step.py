# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""GeminiPresenceStep: row shape, idempotence, flag gating and pipeline placement (no network, no DB)."""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock

from PIL import Image

from app.config import settings
from app.domain.pipeline import GeminiPresenceStep, build_gemini_presence_observation, chunk_bursts
from app.jobs.definitions import build_pipeline_steps
from app.schemas.pipeline import PipelineStepType
from app.services import gemini_presence as gp

MODEL = "gemini-3.1-flash-lite"


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (10, 20, 30)).save(buf, format="JPEG")
    return buf.getvalue()


# ── Pure builder ─────────────────────────────────────────────────────


def test_animal_row_shape_matches_observations_schema_v1():
    v = gp.PresenceVerdict(has_animal=True, confidence=0.87, description="rat at left edge", bbox=(0.1, 0.2, 0.3, 0.4), prompt_version="v1")
    row = build_gemini_presence_observation({"id": "m1"}, "dep1", v, MODEL, "2026-09-26T00:00:00+00:00")
    assert row["media_id"] == "m1" and row["deployment_id"] == "dep1"
    assert row["observation_level"] == "media"
    assert row["observation_type"] == "animal" and row["classifier_category"] == "animal"
    assert row["source_type"] == "ai" and row["ai_origin"] == "cloud"
    assert row["source_model_version"] == MODEL and row["classified_by"] == MODEL
    assert row["review_status"] == "ai_reviewed" and row["classification_method"] == "machine"
    assert row["confidence"] == 0.87
    assert row["observation_comments"] == "rat at left edge"  # a v1 answer keeps the bare description
    assert (row["bbox_x"], row["bbox_y"], row["bbox_w"], row["bbox_h"]) == (0.1, 0.2, 0.3, 0.4)
    assert "id" in row


def test_animal_row_v2_writes_structured_comment_and_derived_confidence():
    v = gp.parse_single_response(
        json.dumps(
            {
                "has_animal": True,
                "description": "dark rodent shape near the lens",
                "bbox": [200, 100, 600, 400],
                "animal_visibility": "obscured",
                "animal_size": "tiny",
                "animal_location": "edge",
                "visual_conditions": ["night_ir", "low_light"],
                "evidence": ["body_outline"],
            }
        )
    )
    row = build_gemini_presence_observation({"id": "m1"}, "dep1", v, MODEL, "t")
    assert row["confidence"] == 0.55  # obscured 0.50 + one evidence item; displayed, never a fusion input
    assert (
        row["observation_comments"]
        == "visibility=obscured; size=tiny; location=edge; conditions=night_ir,low_light; evidence=body_outline | dark rodent shape near the lens"
    )
    assert gp.parse_verdict_comment(row["observation_comments"]) == {
        "visibility": "obscured",
        "size": "tiny",
        "location": "edge",
        "conditions": ["night_ir", "low_light"],
        "evidence": ["body_outline"],
        "description": "dark rodent shape near the lens",
    }
    assert (row["bbox_x"], row["bbox_y"], row["bbox_w"], row["bbox_h"]) == (0.1, 0.2, 0.3, 0.4)


def test_blank_row_has_no_bbox_even_if_model_gave_one():
    v = gp.PresenceVerdict(has_animal=False, confidence=0.6, description="", bbox=(0.1, 0.1, 0.2, 0.2), prompt_version="v1")
    row = build_gemini_presence_observation({"id": "m1"}, "dep1", v, MODEL, "t")
    assert row["observation_type"] == "blank" and row["classifier_category"] == "blank"
    assert not any(k.startswith("bbox_") for k in row)  # chk_bbox_complete: all four or none
    assert "observation_comments" not in row
    v2 = gp.PresenceVerdict(has_animal=False, confidence=0.0, description="leaf litter", bbox=(0.1, 0.1, 0.2, 0.2), visual_conditions=("low_light",))
    row2 = build_gemini_presence_observation({"id": "m1"}, "dep1", v2, MODEL, "t")
    assert not any(k.startswith("bbox_") for k in row2)
    assert row2["observation_comments"] == "visibility=none; conditions=low_light | leaf litter" and row2["confidence"] == 0.0


def test_gemini_evidence_signals_from_a_verdict():
    from app.domain.pipeline import gemini_evidence_signals

    v = gp.PresenceVerdict(has_animal=True, animal_visibility="clear", animal_size="large")
    assert gemini_evidence_signals(v) == {"gemini_presence": 1.0, "gemini_visibility": (1.0, "clear"), "gemini_size": (1.0, "large")}
    blank = gp.PresenceVerdict(has_animal=False)
    assert gemini_evidence_signals(blank) == {"gemini_presence": 0.0, "gemini_visibility": (0.0, "none"), "gemini_size": (0.0, "none")}
    assert gemini_evidence_signals(gp.PresenceVerdict(True, 0.9, prompt_version="v1")) == {"gemini_presence": 1.0}


def test_speciesnet_evidence_signals_keep_the_sub_threshold_max_conf():
    from types import SimpleNamespace

    from app.domain.pipeline import speciesnet_evidence_signals

    pred = SimpleNamespace(detections=[SimpleNamespace(confidence=0.14), SimpleNamespace(confidence=0.09)])
    assert speciesnet_evidence_signals(pred, [{"observation_type": "blank"}]) == {"speciesnet_presence": 0.0, "speciesnet_max_conf": 0.14}
    assert speciesnet_evidence_signals(SimpleNamespace(detections=[]), [{"observation_type": "blank"}]) == {
        "speciesnet_presence": 0.0,
        "speciesnet_max_conf": 0.0,
    }
    kept = SimpleNamespace(detections=[SimpleNamespace(confidence=0.71)])
    assert speciesnet_evidence_signals(kept, [{"observation_type": "human"}]) == {"speciesnet_presence": 1.0, "speciesnet_max_conf": 0.71}


def test_bbox_written_only_as_a_complete_valid_quad():
    bad = gp.PresenceVerdict(has_animal=True, confidence=0.9, bbox=(0.9, 0.9, 0.0, 0.1))  # zero width
    assert not any(k.startswith("bbox_") for k in build_gemini_presence_observation({"id": "m"}, "d", bad, MODEL, "t"))
    none = gp.PresenceVerdict(has_animal=True, confidence=1.2, bbox=None)
    row = build_gemini_presence_observation({"id": "m"}, "d", none, MODEL, "t")
    assert row["confidence"] == 1.0  # clamped into the CHECK range
    assert not any(k.startswith("bbox_") for k in row)


def test_chunk_bursts_caps_contact_sheet_size():
    bursts = [[{"id": str(i)} for i in range(8)], [{"id": "x"}]]
    chunks = chunk_bursts(bursts, 6)
    assert [len(c) for c in chunks] == [6, 2, 1]


# ── Step run ─────────────────────────────────────────────────────────


def _fake_svc(existing_media_ids: set[str], inserted: list[list[dict]]) -> MagicMock:
    """Supabase stand-in: select(...).in_(...).eq(...).execute() -> existing rows; insert(...).execute() records."""
    table = MagicMock()
    table.select.return_value = table
    table.in_.return_value = table
    table.eq.return_value = table
    table.execute.return_value = MagicMock(data=[{"media_id": m} for m in existing_media_ids])

    def _insert(rows):
        inserted.append(rows)
        ins = MagicMock()
        ins.execute.return_value = MagicMock(data=rows)
        return ins

    table.insert.side_effect = _insert
    svc = MagicMock()
    svc.table.return_value = table
    return svc


async def test_step_writes_one_row_per_frame_and_skips_already_labelled(monkeypatch):
    monkeypatch.setattr(settings, "FF_GEMINI_PRESENCE_ENABLED", True)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "GEMINI_PRESENCE_MODEL", MODEL)
    monkeypatch.setattr(settings, "GEMINI_PRESENCE_VARIANT", "single")
    inserted: list[list[dict]] = []
    monkeypatch.setattr("app.domain.pipeline.create_service_client", lambda: _fake_svc({"m2"}, inserted))

    async def fake_resolve(path, size="full"):
        return (_jpeg(), "image/jpeg")

    monkeypatch.setattr("app.domain.media_resolver.resolve_media", fake_resolve)

    called: list[int] = []

    def fake_presence(images, variant, model, **kw):
        called.append(len(images))
        v = gp.PresenceVerdict(has_animal=True, confidence=0.9, description="cat", bbox=(0.1, 0.1, 0.5, 0.5))
        return gp.PresenceResult(model=model, variant=variant, verdicts=[v], usage=gp.TokenUsage(300, 40, 10, 350), cost_usd=0.0002, latency_s=0.5)

    monkeypatch.setattr(gp, "presence", fake_presence)

    media = [
        {"id": "m1", "file_path": "azure://a", "timestamp": "2026-09-26T00:00:00+00:00"},
        {"id": "m2", "file_path": "azure://b", "timestamp": "2026-09-26T00:00:01+00:00"},  # already has a Gemini row
        {"id": "m3", "file_path": "azure://c", "timestamp": "2026-09-26T00:00:02+00:00"},
    ]
    result = await GeminiPresenceStep().run(media, "dep1", {"confidence_threshold": 0.2})

    assert called == [1, 1]  # m2 skipped (idempotent), one call per frame
    rows = [r for batch in inserted for r in batch]
    assert {r["media_id"] for r in rows} == {"m1", "m3"}
    assert all(r["source_model_version"] == MODEL and r["observation_type"] == "animal" for r in rows)
    assert result.step == PipelineStepType.GEMINI_PRESENCE
    assert result.observations_created == 2 and result.media_processed == 3 and result.errors == 0
    assert result.input_tokens == 600 and result.output_tokens == 100
    assert result.cost_usd == 0.0004
    assert result.model_version == MODEL


async def test_step_contact_sheet_groups_a_burst_into_one_call(monkeypatch):
    monkeypatch.setattr(settings, "FF_GEMINI_PRESENCE_ENABLED", True)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "GEMINI_PRESENCE_VARIANT", "contact_sheet")
    monkeypatch.setattr(settings, "BURST_GAP_SECONDS", 10.0)
    inserted: list[list[dict]] = []
    monkeypatch.setattr("app.domain.pipeline.create_service_client", lambda: _fake_svc(set(), inserted))

    async def fake_resolve(path, size="full"):
        return (_jpeg(), "image/jpeg")

    monkeypatch.setattr("app.domain.media_resolver.resolve_media", fake_resolve)
    calls: list[int] = []

    def fake_presence(images, variant, model, **kw):
        calls.append(len(images))
        verdicts = [gp.PresenceVerdict(has_animal=i == 0, confidence=0.7) for i in range(len(images))]
        return gp.PresenceResult(model=model, variant=variant, verdicts=verdicts, usage=gp.TokenUsage(400, 90, 0, 490), cost_usd=0.0003)

    monkeypatch.setattr(gp, "presence", fake_presence)

    media = [
        {"id": "a", "file_path": "x", "timestamp": "2026-09-26T00:00:00+00:00"},
        {"id": "b", "file_path": "x", "timestamp": "2026-09-26T00:00:01+00:00"},
        {"id": "c", "file_path": "x", "timestamp": "2026-09-26T00:10:00+00:00"},  # a new trigger
    ]
    result = await GeminiPresenceStep().run(media, "dep1", {})
    assert calls == [2, 1]
    rows = [r for batch in inserted for r in batch]
    assert [(r["media_id"], r["observation_type"]) for r in rows] == [("a", "animal"), ("b", "blank"), ("c", "animal")]
    assert result.observations_created == 3


async def test_step_noops_when_flag_or_key_missing(monkeypatch):
    monkeypatch.setattr(settings, "FF_GEMINI_PRESENCE_ENABLED", False)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "k")
    boom = MagicMock(side_effect=AssertionError("must not touch the DB"))
    monkeypatch.setattr("app.domain.pipeline.create_service_client", boom)
    result = await GeminiPresenceStep().run([{"id": "m1", "file_path": "x"}], "dep1", {})
    assert result.media_processed == 0 and result.observations_created == 0
    monkeypatch.setattr(settings, "FF_GEMINI_PRESENCE_ENABLED", True)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    result = await GeminiPresenceStep().run([{"id": "m1", "file_path": "x"}], "dep1", {})
    assert result.media_processed == 0
    boom.assert_not_called()


async def test_step_counts_a_failed_call_as_error_and_keeps_going(monkeypatch):
    monkeypatch.setattr(settings, "FF_GEMINI_PRESENCE_ENABLED", True)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(settings, "GEMINI_PRESENCE_VARIANT", "single")
    inserted: list[list[dict]] = []
    monkeypatch.setattr("app.domain.pipeline.create_service_client", lambda: _fake_svc(set(), inserted))

    async def fake_resolve(path, size="full"):
        return (_jpeg(), "image/jpeg")

    monkeypatch.setattr("app.domain.media_resolver.resolve_media", fake_resolve)
    n = {"i": 0}

    def flaky(images, variant, model, **kw):
        n["i"] += 1
        if n["i"] == 1:
            raise RuntimeError("503 from the API")
        return gp.PresenceResult(
            model=model, variant=variant, verdicts=[gp.PresenceVerdict(False, 0.9)], usage=gp.TokenUsage(1, 1, 0, 2), cost_usd=0.0
        )

    monkeypatch.setattr(gp, "presence", flaky)
    result = await GeminiPresenceStep().run([{"id": "m1", "file_path": "x"}, {"id": "m2", "file_path": "x"}], "dep1", {})
    assert result.errors == 1 and result.observations_created == 1
    assert [r["media_id"] for b in inserted for r in b] == ["m2"]


# ── Wiring ───────────────────────────────────────────────────────────


def test_build_pipeline_steps_places_gemini_before_speciesnet(monkeypatch):
    monkeypatch.setattr(settings, "FF_ML_ENABLED", True)
    monkeypatch.setattr(settings, "FF_PIPELINE_ENABLED", True)
    monkeypatch.setattr(settings, "FF_MEDIA_REGISTRY_ENABLED", False)
    monkeypatch.setattr(settings, "FF_SPECIESNET_ENABLED", True)
    monkeypatch.setattr(settings, "FF_BIOCLIP_ENABLED", False)
    monkeypatch.setattr(settings, "FF_GEMINI_PRESENCE_ENABLED", True)
    steps = build_pipeline_steps()
    assert steps.index(PipelineStepType.GEMINI_PRESENCE) < steps.index(PipelineStepType.SPECIESNET)
    monkeypatch.setattr(settings, "FF_GEMINI_PRESENCE_ENABLED", False)
    assert PipelineStepType.GEMINI_PRESENCE not in build_pipeline_steps()
