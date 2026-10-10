# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Gemini presence service: image preparation, JSON parsing, cost, and the mocked network paths.

No network: the three SDK boundary functions are monkeypatched with fakes.
"""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from app.services import gemini_presence as gp
from app.services.gemini_pricing import (
    MEDIA_RESOLUTION_TOKENS,
    TILE_TOKENS,
    cost_usd,
    estimate_image_tokens,
    model_price,
)


def _jpeg(w=640, h=480, colour=(40, 90, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(buf, format="JPEG")
    return buf.getvalue()


def _size(data: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(data)).size


# ── Image preparation ────────────────────────────────────────────────


def test_prepare_single_leaves_a_ww500_frame_as_one_tile():
    p = gp.prepare_single(_jpeg(640, 480))
    assert (p.width, p.height) == (640, 480)
    assert _size(p.data) == (640, 480)
    assert p.cell_boxes == ((0, 0, 640, 480),)


def test_prepare_single_downscales_to_768_longest_side_keeping_aspect():
    p = gp.prepare_single(_jpeg(1600, 1200))
    assert (p.width, p.height) == (768, 576)
    assert _size(p.data) == (768, 576)


@pytest.mark.parametrize("n,expected", [(1, (1, 1)), (2, (2, 1)), (3, (3, 1)), (4, (3, 2)), (6, (3, 2))])
def test_sheet_layout(n, expected):
    assert gp.sheet_layout(n) == expected


def test_prepare_contact_sheet_geometry_and_cell_boxes():
    frames = [_jpeg(640, 480, (i * 30, 0, 0)) for i in range(5)]
    sheet = gp.prepare_contact_sheet(frames)
    cw, ch = gp.SHEET_CELL_SIZE
    assert sheet.width == 3 * cw + 2 * gp.SHEET_GUTTER
    assert sheet.height == 2 * ch + gp.SHEET_GUTTER
    assert _size(sheet.data) == (sheet.width, sheet.height)
    assert len(sheet.cell_boxes) == 5
    # Every frame lands inside its own grid cell; 4:3 frames fill a 384x288 cell exactly.
    for idx, (x0, y0, x1, y1) in enumerate(sheet.cell_boxes):
        col, row = idx % 3, idx // 3
        cx0, cy0 = col * (cw + gp.SHEET_GUTTER), row * (ch + gp.SHEET_GUTTER)
        assert (x0, y0, x1, y1) == (cx0, cy0, cx0 + cw, cy0 + ch)
    assert len(set(sheet.cell_boxes)) == 5


def test_prepare_contact_sheet_rejects_empty_and_oversized():
    with pytest.raises(ValueError):
        gp.prepare_contact_sheet([])
    with pytest.raises(ValueError):
        gp.prepare_contact_sheet([_jpeg()] * (gp.SHEET_MAX_CELLS + 1))


def test_prepare_dispatches_by_variant():
    assert len(gp.prepare([_jpeg()], "single").cell_boxes) == 1
    assert len(gp.prepare([_jpeg()], "batch").cell_boxes) == 1
    assert len(gp.prepare([_jpeg(), _jpeg()], "contact_sheet").cell_boxes) == 2
    with pytest.raises(ValueError):
        gp.prepare([_jpeg(), _jpeg()], "single")
    with pytest.raises(ValueError):
        gp.prepare([_jpeg()], "nope")


# ── Bounding boxes ───────────────────────────────────────────────────


def test_bbox_1000_to_xywh_converts_and_rejects():
    assert gp.bbox_1000_to_xywh([100, 200, 500, 600]) == (0.2, 0.1, 0.4, 0.4)
    assert gp.bbox_1000_to_xywh(None) is None
    assert gp.bbox_1000_to_xywh([1, 2, 3]) is None
    assert gp.bbox_1000_to_xywh(["a", 0, 1, 1]) is None
    assert gp.bbox_1000_to_xywh([500, 500, 100, 100]) is None  # inverted
    # Out-of-range values are clamped rather than dropped.
    assert gp.bbox_1000_to_xywh([-10, 0, 1200, 1000]) == (0.0, 0.0, 1.0, 1.0)


def test_map_sheet_bbox_to_frame_uses_the_cell_geometry():
    sheet = gp.prepare_contact_sheet([_jpeg() for _ in range(4)])  # 3x2 grid, cells 384x288
    # Cell index 3 (second row, first column) fully covered: whole-sheet box in 0-1000.
    x0, y0, x1, y1 = sheet.cell_boxes[3]
    raw = [y0 / sheet.height * 1000, x0 / sheet.width * 1000, y1 / sheet.height * 1000, x1 / sheet.width * 1000]
    mapped = gp.map_sheet_bbox_to_frame(raw, sheet, 3)
    assert mapped is not None
    assert all(abs(a - b) < 0.02 for a, b in zip(mapped, (0.0, 0.0, 1.0, 1.0)))
    # The same box does not overlap cell 0 (top-left), so it maps to nothing there.
    assert gp.map_sheet_bbox_to_frame(raw, sheet, 0) is None
    # A box in the left half of cell 0 maps to x in [0, 0.5].
    half = [0, 0, 288 / sheet.height * 1000, 192 / sheet.width * 1000]
    mx, my, mw, mh = gp.map_sheet_bbox_to_frame(half, sheet, 0)
    assert (mx, my) == (0.0, 0.0)
    assert abs(mw - 0.5) < 0.01 and abs(mh - 1.0) < 0.01


# ── Parsing ──────────────────────────────────────────────────────────


def test_parse_single_response_happy_path():
    v = gp.parse_single_response(json.dumps({"has_animal": True, "confidence": 0.83, "description": "a rat", "bbox": [100, 200, 500, 600]}), "v1")
    assert v.has_animal is True and v.confidence == 0.83 and v.description == "a rat"
    assert v.bbox == (0.2, 0.1, 0.4, 0.4)
    assert v.prompt_version == "v1" and v.animal_visibility == "partial"  # an animal with no visibility given


def test_parse_single_response_v2_structured_fields_and_derived_confidence():
    text = json.dumps(
        {
            "has_animal": True,
            "animal_visibility": "partial",
            "animal_size": "small",
            "animal_location": "edge",
            "visual_conditions": ["night_ir", "low_light", "not_a_condition", "night_ir"],
            "evidence": ["tail", "fur_texture", "head", "limb", "none"],
            "description": "A small rodent very close to the lens.",
            "bbox": [600, 0, 1000, 400],
        }
    )
    v = gp.parse_single_response(text, "v2")
    assert v.prompt_version == "v2"
    assert (v.animal_visibility, v.animal_size, v.animal_location) == ("partial", "small", "edge")
    assert v.visual_conditions == ("night_ir", "low_light")  # unknown dropped, duplicate collapsed
    assert v.evidence == ("tail", "fur_texture", "head", "limb", "none") and v.evidence_items == ("tail", "fur_texture", "head", "limb")
    assert v.confidence == pytest.approx(0.85)  # partial 0.70 + 0.05 x min(3, 4)
    assert v.bbox == (0.0, 0.6, 0.4, 0.4)
    assert gp.derived_confidence("clear", []) == 0.85 and gp.derived_confidence("obscured", ["eye_shine"]) == 0.55
    assert gp.derived_confidence("clear", ["a", "b", "c", "d"]) == 1.0 and gp.derived_confidence("none", ["none"]) == 0.0


def test_parse_single_response_v2_consistency_rules():
    empty = gp.parse_single_response(
        json.dumps({"has_animal": False, "animal_visibility": "clear", "animal_size": "large", "animal_location": "centre"}), "v2"
    )
    assert (empty.animal_visibility, empty.animal_size, empty.animal_location, empty.confidence) == ("none", "none", "none", 0.0)
    # An animal answer must carry a visibility from the vocabulary: a stray label is a parse error, never a silent category.
    with pytest.raises(gp.PresenceParseError):
        gp.parse_single_response(json.dumps({"has_animal": True, "animal_visibility": "none"}), "v2")
    with pytest.raises(gp.PresenceParseError):
        gp.parse_single_response(json.dumps({"has_animal": True, "animal_visibility": "kind of"}), "v2")
    with pytest.raises(gp.PresenceParseError):
        gp.parse_single_response(json.dumps({"has_animal": True}), "v2")
    # Size and location only feed strata: an unknown value falls back to none.
    loose = gp.parse_single_response(
        json.dumps({"has_animal": True, "animal_visibility": "Clear", "animal_size": "HUGE", "animal_location": 3}), "v2"
    )
    assert (loose.animal_visibility, loose.animal_size, loose.animal_location, loose.confidence) == ("clear", "none", "none", 0.85)
    # A v1 answer has no visibility field: partial by convention, the model's own confidence.
    v1 = gp.parse_single_response(json.dumps({"has_animal": True, "confidence": 0.9, "description": "x"}), "v1")
    assert v1.animal_visibility == "partial" and v1.confidence == 0.9 and v1.animal_size == "none"


def test_format_and_parse_verdict_comment_roundtrip():
    v = gp.PresenceVerdict(True, None, "a rat", None, "clear", "small", "corner", ("night_ir", "none"), ("tail", "fur_texture", "none"))
    text = gp.format_verdict_comment(v)
    assert text == "visibility=clear; size=small; location=corner; conditions=night_ir; evidence=tail,fur_texture | a rat"
    assert gp.parse_verdict_comment(text) == {
        "visibility": "clear",
        "size": "small",
        "location": "corner",
        "conditions": ["night_ir"],
        "evidence": ["tail", "fur_texture"],
        "description": "a rat",
    }
    assert gp.format_verdict_comment(gp.PresenceVerdict(False)) == "visibility=none"
    assert gp.parse_verdict_comment("visibility=none") == {"visibility": "none", "description": ""}
    assert gp.parse_verdict_comment("A black cat is walking across a paved patio area") == {}  # a v1 description
    assert gp.parse_verdict_comment(None) == {}
    assert gp.parse_verdict_comment("bogus=1; visibility=partial | x | y") == {"visibility": "partial", "description": "x | y"}


def test_parse_single_response_tolerates_fences_lists_and_string_booleans():
    fenced = '```json\n{"has_animal": "true", "confidence": "0.5", "description": "x", "bbox": null}\n```'
    v = gp.parse_single_response(fenced, "v1")
    assert v.has_animal is True and v.confidence == 0.5 and v.bbox is None
    v2 = gp.parse_single_response(json.dumps([{"has_animal": False, "confidence": 2.0, "description": ""}]), "v1")
    assert v2.has_animal is False and v2.confidence == 1.0  # clamped
    fenced_v2 = '```json\n{"has_animal": "yes", "animal_visibility": "clear", "description": "x", "bbox": null}\n```'
    assert gp.parse_single_response(fenced_v2).has_animal is True


@pytest.mark.parametrize("bad", ["", "   ", "not json", '{"confidence": 0.4}', "[1, 2]", '{"has_animal": true'])
def test_parse_single_response_malformed_raises(bad):
    with pytest.raises(gp.PresenceParseError):
        gp.parse_single_response(bad)


def test_parse_sheet_response_maps_cells_by_burned_index():
    sheet = gp.prepare_contact_sheet([_jpeg() for _ in range(3)])
    text = json.dumps(
        {
            "cells": [
                {"index": 2, "has_animal": True, "animal_visibility": "clear", "description": "possum"},
                {"index": 1, "has_animal": False, "description": "empty"},
                {"index": 2, "has_animal": False, "description": "duplicate, ignored"},
                {"index": 9, "has_animal": True, "animal_visibility": "clear", "description": "out of range, ignored"},
            ]
        }
    )
    verdicts = gp.parse_sheet_response(text, sheet)
    assert len(verdicts) == 3
    assert verdicts[0].has_animal is False
    assert verdicts[1].has_animal is True and verdicts[1].description == "possum"
    assert verdicts[2] is None  # the model did not answer cell 3


def test_parse_sheet_response_malformed_raises():
    sheet = gp.prepare_contact_sheet([_jpeg()])
    with pytest.raises(gp.PresenceParseError):
        gp.parse_sheet_response('{"cells": 3}', sheet)
    with pytest.raises(gp.PresenceParseError):
        gp.parse_sheet_response("nope", sheet)


def test_prompt_and_schema_per_variant():
    assert "contact sheet of 4" in gp.build_prompt("contact_sheet", 4)
    assert "cells" in gp.response_json_schema("contact_sheet")["properties"]
    assert "has_animal" in gp.response_json_schema("single")["properties"]
    assert "[ymin, xmin, ymax, xmax]" in gp.build_prompt("single")


def test_prompt_v1_is_the_default():
    assert gp.PROMPT_VERSION == "v1" and gp.PROMPT_VERSIONS == ("v1", "v2", "v3")
    assert gp.build_prompt("single") == gp.build_prompt("single", prompt_version="v1")
    assert "confidence" in gp.response_json_schema("single")["properties"]
    assert gp.PresenceVerdict(True).prompt_version == gp.PresenceResult("m", "single", []).prompt_version == "v1"


def test_prompt_v2_carries_the_near_lens_cue():
    prompt = gp.build_prompt("single", prompt_version="v2")
    assert "Small rodents very close to the lens on night IR frames" in prompt and "no eye shine" in prompt
    assert prompt != gp.build_prompt("single", prompt_version="v1")
    assert "confidence" in gp.build_prompt("single", prompt_version="v1") and "confidence" not in prompt
    for value in gp.VISIBILITY_VALUES + gp.SIZE_VALUES + gp.LOCATION_VALUES + gp.VISUAL_CONDITIONS + gp.EVIDENCE_VALUES:
        assert f'"{value}"' in prompt
    sheet = gp.build_prompt("contact_sheet", 3, "v2")
    assert "contact sheet of 3" in sheet and "no eye shine" in sheet
    with pytest.raises(ValueError):
        gp.build_prompt("single", prompt_version="v9")


def test_prompt_v3_is_animals_only_short_and_asks_for_has_person():
    prompt = gp.build_prompt("single", prompt_version="v3")
    assert "A person is never an animal" in prompt and "has_person" in prompt and "at most 8 words" in prompt
    assert "night IR" in prompt and "confidence" not in prompt
    assert len(prompt) < len(gp.build_prompt("single", prompt_version="v2"))
    schema = gp.response_json_schema("single", "v3")
    assert list(schema["properties"]) == ["has_person", "has_animal", "animal_visibility", "description", "bbox"]  # person first
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["animal_visibility"]["enum"] == list(gp.VISIBILITY_VALUES)
    cell = gp.response_json_schema("contact_sheet", "v3")["properties"]["cells"]["items"]
    assert "index" in cell["required"] and "has_person" in cell["properties"]
    assert "contact sheet of 2" in gp.build_prompt("contact_sheet", 2, "v3")


def test_parse_v3_reads_has_person_and_keeps_people_out_of_has_animal():
    person = gp.parse_single_response(
        json.dumps({"has_person": True, "has_animal": False, "animal_visibility": "none", "description": "a hand", "bbox": None}), "v3"
    )
    assert person.has_person is True and person.has_animal is False and person.prompt_version == "v3" and person.confidence == 0.0
    rat = gp.parse_single_response(
        json.dumps({"has_person": "false", "has_animal": True, "animal_visibility": "obscured", "description": "rodent", "bbox": [0, 0, 500, 500]}),
        "v3",
    )
    assert rat.has_person is False and rat.has_animal is True and rat.confidence == 0.5 and rat.animal_size == "none"
    assert gp.parse_single_response(json.dumps({"has_animal": False}), "v3").has_person is False  # left out reads as no
    with pytest.raises(gp.PresenceParseError):  # an animal answer still needs a visibility
        gp.parse_single_response(json.dumps({"has_person": False, "has_animal": True}), "v3")
    assert gp.parse_single_response(json.dumps({"has_animal": True, "confidence": 1.0}), "v1").has_person is None
    assert gp.format_verdict_comment(person) == "visibility=none; person=yes | a hand"


def test_schema_v2_enumerates_every_string_and_has_no_confidence():
    schema = gp.response_json_schema("single", "v2")
    props = schema["properties"]
    assert "confidence" not in props
    assert gp.VISIBILITY_VALUES == ("clear", "partial", "obscured", "none")
    assert gp.SIZE_VALUES == ("tiny", "small", "medium", "large", "none")
    assert gp.LOCATION_VALUES == ("centre", "edge", "corner", "none")
    assert props["animal_visibility"] == {"type": "string", "enum": list(gp.VISIBILITY_VALUES)}
    assert props["animal_size"] == {"type": "string", "enum": list(gp.SIZE_VALUES)}
    assert props["animal_location"] == {"type": "string", "enum": list(gp.LOCATION_VALUES)}
    assert props["visual_conditions"]["items"]["enum"] == list(gp.VISUAL_CONDITIONS) and "none" in gp.VISUAL_CONDITIONS
    assert props["evidence"]["items"]["enum"] == list(gp.EVIDENCE_VALUES) and gp.EVIDENCE_VALUES[0] == "eye_shine"
    assert set(schema["required"]) == {
        "has_animal",
        "animal_visibility",
        "animal_size",
        "animal_location",
        "visual_conditions",
        "evidence",
        "description",
        "bbox",
    }
    cell = gp.response_json_schema("contact_sheet", "v2")["properties"]["cells"]["items"]
    assert "index" in cell["properties"] and "animal_visibility" in cell["properties"] and "confidence" not in cell["properties"]
    assert "confidence" in gp.response_json_schema("single", "v1")["properties"]


def test_estimate_call_v2_assumes_a_longer_answer():
    prepared = gp.prepare_single(_jpeg())
    _inp1, out1, usd1 = gp.estimate_call(prepared, "gemini-3.1-flash-lite", "single", prompt_version="v1")
    _inp2, out2, usd2 = gp.estimate_call(prepared, "gemini-3.1-flash-lite", "single", prompt_version="v2")
    assert out2 > out1 and usd2 > usd1
    assert gp.estimate_call(prepared, "gemini-3.1-flash-lite", "single")[1] == out1  # the default is v1


# ── Tokens and cost ──────────────────────────────────────────────────


def test_token_usage_from_usage_metadata_object_and_dict():
    obj = SimpleNamespace(prompt_token_count=300, candidates_token_count=40, thoughts_token_count=None, total_token_count=340)
    u = gp.TokenUsage.from_usage_metadata(obj)
    assert (u.input_tokens, u.output_tokens, u.thought_tokens, u.total_tokens) == (300, 40, 0, 340)
    d = gp.TokenUsage.from_usage_metadata({"prompt_token_count": 10, "candidates_token_count": 5, "thoughts_token_count": 7})
    assert d.billed_output_tokens == 12
    assert gp.TokenUsage.from_usage_metadata(None) == gp.TokenUsage()


def test_cost_from_price_table_standard_and_batch():
    price = model_price("gemini-3.1-flash-lite")
    assert (price.input_usd_per_m, price.output_usd_per_m) == (0.25, 1.50)
    assert cost_usd("gemini-3.1-flash-lite", 1000, 100) == pytest.approx(0.00025 + 0.00015)
    assert cost_usd("gemini-3.1-flash-lite", 1000, 100, batch=True) == pytest.approx((0.00025 + 0.00015) / 2)
    with pytest.raises(ValueError):
        cost_usd("gemini-9-unknown", 1, 1)


def test_estimate_image_tokens_tile_rule_and_media_resolution():
    assert estimate_image_tokens(640, 480, "gemini-2.5-flash-lite") == TILE_TOKENS
    assert estimate_image_tokens(300, 300, "gemini-2.5-flash-lite") == TILE_TOKENS
    assert estimate_image_tokens(1600, 1200, "gemini-2.5-flash-lite") == 3 * 2 * TILE_TOKENS
    assert estimate_image_tokens(640, 480, "gemini-3.1-flash-lite", "low") == MEDIA_RESOLUTION_TOKENS["low"]
    assert estimate_image_tokens(4000, 3000, "gemini-3.1-flash-lite", "high") == 1120


def test_estimate_call_dry_run_has_no_network():
    prepared = gp.prepare_single(_jpeg())
    inp, out, usd = gp.estimate_call(prepared, "gemini-3.1-flash-lite", "single")
    assert inp > MEDIA_RESOLUTION_TOKENS["low"] and out > 0 and usd > 0
    _inp, _out, usd_batch = gp.estimate_call(prepared, "gemini-3.1-flash-lite", "batch")
    assert usd_batch == pytest.approx(usd / 2)


# ── Network boundary (mocked) ────────────────────────────────────────


def _fake_response(text: str, prompt=300, out=40, thoughts=0):
    return SimpleNamespace(
        text=text,
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt, candidates_token_count=out, thoughts_token_count=thoughts, total_token_count=prompt + out + thoughts
        ),
    )


def test_presence_single_uses_generate_and_prices_the_call(monkeypatch):
    seen = {}

    def fake_generate(model, contents, config):
        seen["model"] = model
        seen["config"] = config
        seen["parts"] = contents
        return _fake_response(json.dumps({"has_animal": True, "confidence": 0.9, "description": "kiwi", "bbox": [0, 0, 500, 500]}), thoughts=10)

    monkeypatch.setattr(gp, "_generate_content", fake_generate)
    result = gp.presence([_jpeg()], "single", "gemini-3.1-flash-lite", thinking_level="low", prompt_version="v1")
    assert seen["model"] == "gemini-3.1-flash-lite"
    assert seen["config"].response_mime_type == "application/json"
    assert seen["config"].response_json_schema["properties"]["has_animal"] == {"type": "boolean"}
    assert "confidence" in seen["config"].response_json_schema["properties"] and result.prompt_version == "v1"
    level = seen["config"].thinking_config.thinking_level
    assert str(getattr(level, "value", level)).lower() == "low"  # the SDK coerces it to its enum
    assert len(seen["parts"]) == 2 and seen["parts"][1].inline_data.mime_type == "image/jpeg"
    assert result.verdicts[0].has_animal is True and result.verdicts[0].bbox == (0.0, 0.0, 0.5, 0.5)
    assert result.usage.input_tokens == 300 and result.usage.billed_output_tokens == 50
    assert result.cost_usd == pytest.approx(cost_usd("gemini-3.1-flash-lite", 300, 50))
    assert result.latency_s >= 0 and result.error is None


class _QuotaError(Exception):
    code = 429


def test_presence_waits_out_a_quota_error_then_retries(monkeypatch):
    calls = {"n": 0}
    waits: list[float] = []

    def fake_generate(model, contents, config):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _QuotaError("429 RESOURCE_EXHAUSTED. Quota exceeded ... Please retry in 2.5s.")
        return _fake_response(json.dumps({"has_animal": False, "confidence": 0.8, "description": "leaf litter", "bbox": None}))

    monkeypatch.setattr(gp, "_generate_content", fake_generate)
    result = gp.presence([_jpeg()], "single", "gemini-3.1-flash-lite", sleep=waits.append)
    assert calls["n"] == 2 and waits == [3.5]  # the server's delay plus one second
    assert result.verdicts[0].has_animal is False and result.error is None


def test_presence_waits_out_a_dropped_connection(monkeypatch):
    class ConnectError(Exception):  # same name as httpx's, which is what the check keys on
        pass

    calls = {"n": 0}
    waits: list[float] = []

    def fake_generate(model, contents, config):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectError("[Errno 11001] getaddrinfo failed")
        return _fake_response(
            json.dumps({"has_animal": True, "animal_visibility": "partial", "evidence": ["tail"], "description": "rat", "bbox": None})
        )

    monkeypatch.setattr(gp, "_generate_content", fake_generate)
    result = gp.presence([_jpeg()], "single", "gemini-3.1-flash-lite", sleep=waits.append, prompt_version="v2")
    assert calls["n"] == 3 and waits == [gp.QUOTA_DEFAULT_WAIT_S] * 2
    assert result.verdicts[0].has_animal is True and result.verdicts[0].confidence == 0.75


def test_presence_gives_up_after_the_retry_budget_and_reraises_other_errors(monkeypatch):
    monkeypatch.setattr(gp, "_generate_content", lambda m, c, cfg: (_ for _ in ()).throw(_QuotaError("RESOURCE_EXHAUSTED")))
    waits: list[float] = []
    with pytest.raises(_QuotaError):
        gp.presence([_jpeg()], "single", "gemini-3.1-flash-lite", sleep=waits.append)
    assert len(waits) == gp.QUOTA_RETRY_ATTEMPTS - 1 and set(waits) == {gp.QUOTA_DEFAULT_WAIT_S}

    monkeypatch.setattr(gp, "_generate_content", lambda m, c, cfg: (_ for _ in ()).throw(ValueError("not a quota problem")))
    with pytest.raises(ValueError):
        gp.presence([_jpeg()], "single", "gemini-3.1-flash-lite", sleep=lambda s: pytest.fail("must not wait"))


def test_presence_malformed_answer_sets_error_not_exception(monkeypatch):
    monkeypatch.setattr(gp, "_generate_content", lambda m, c, cfg: _fake_response("sorry, no"))
    result = gp.presence([_jpeg()], "single", "gemini-3.1-flash-lite")
    assert result.verdicts == [None] and result.error
    assert result.cost_usd > 0  # the tokens were still billed


def test_presence_contact_sheet_returns_one_verdict_per_frame(monkeypatch):
    seen = {}

    def fake_generate(model, contents, config):
        seen["schema"] = config.response_json_schema
        cells = [
            {"index": i + 1, "has_animal": i == 1, "animal_visibility": "clear" if i == 1 else "none", "animal_size": "medium", "description": "c"}
            for i in range(3)
        ]
        return _fake_response(json.dumps({"cells": cells}))

    monkeypatch.setattr(gp, "_generate_content", fake_generate)
    result = gp.presence([_jpeg(), _jpeg(), _jpeg()], "contact_sheet", "gemini-3.1-flash-lite", prompt_version="v2")
    assert [v.has_animal for v in result.verdicts] == [False, True, False]
    assert [v.animal_visibility for v in result.verdicts] == ["none", "clear", "none"]
    assert [v.animal_size for v in result.verdicts] == ["none", "medium", "none"]
    assert [v.confidence for v in result.verdicts] == [0.0, 0.85, 0.0]
    assert "confidence" not in seen["schema"]["properties"]["cells"]["items"]["properties"]
    assert result.prompt_version == "v2" and all(v.prompt_version == "v2" for v in result.verdicts)


def test_presence_batch_polls_and_prices_at_batch_tier(monkeypatch):
    calls = {"create": None, "gets": 0, "sleeps": []}
    good = _fake_response(json.dumps({"has_animal": False, "confidence": 0.95, "description": "leaf litter"}))

    def fake_create(model, src, display_name):
        calls["create"] = (model, len(src), display_name)
        return SimpleNamespace(name="batches/abc", state=SimpleNamespace(name="JOB_STATE_PENDING"))

    def fake_get(name):
        calls["gets"] += 1
        if calls["gets"] < 2:
            return SimpleNamespace(name=name, state=SimpleNamespace(name="JOB_STATE_RUNNING"))
        return SimpleNamespace(
            name=name,
            state=SimpleNamespace(name="JOB_STATE_SUCCEEDED"),
            dest=SimpleNamespace(inlined_responses=[SimpleNamespace(response=good, error=None), SimpleNamespace(response=None, error="quota")]),
        )

    monkeypatch.setattr(gp, "_batch_create", fake_create)
    monkeypatch.setattr(gp, "_batch_get", fake_get)
    results = gp.presence_batch([[_jpeg()], [_jpeg()]], "batch", "gemini-3.1-flash-lite", poll_seconds=1, sleep=lambda s: calls["sleeps"].append(s))
    assert calls["create"] == ("gemini-3.1-flash-lite", 2, "ww-presence")
    assert calls["sleeps"] == [1, 1]
    assert len(results) == 2
    assert results[0].verdicts[0].has_animal is False
    assert results[0].cost_usd == pytest.approx(cost_usd("gemini-3.1-flash-lite", 300, 40, batch=True))
    assert results[1].verdicts == [None] and "quota" in results[1].error


def test_presence_batch_times_out_when_job_never_finishes(monkeypatch):
    monkeypatch.setattr(gp, "_batch_create", lambda m, s, d: SimpleNamespace(name="b", state=SimpleNamespace(name="JOB_STATE_PENDING")))
    monkeypatch.setattr(gp, "_batch_get", lambda n: SimpleNamespace(name=n, state=SimpleNamespace(name="JOB_STATE_PENDING")))
    with pytest.raises(TimeoutError):
        gp.presence_batch([[_jpeg()]], "batch", "gemini-3.1-flash-lite", poll_seconds=0, max_wait_seconds=-1, sleep=lambda s: None)


def test_presence_batch_refuses_to_poll_a_job_without_a_name(monkeypatch):
    # Polling used to call _batch_get(None) and fail inside the SDK with an unrelated error.
    monkeypatch.setattr(gp, "_batch_create", lambda m, s, d: SimpleNamespace(name=None, state=SimpleNamespace(name="JOB_STATE_PENDING")))
    monkeypatch.setattr(gp, "_batch_get", lambda n: pytest.fail("polled a job with no name"))
    with pytest.raises(RuntimeError, match="without a name"):
        gp.presence_batch([[_jpeg()]], "batch", "gemini-3.1-flash-lite", poll_seconds=0, sleep=lambda s: None)


def test_is_enabled_follows_the_api_key(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    assert gp.is_enabled() is False
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key")
    assert gp.is_enabled() is True
